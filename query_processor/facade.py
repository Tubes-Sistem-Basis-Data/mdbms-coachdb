from __future__ import annotations

from datetime import datetime
import logging
import re

from common.errors import (
    MdbmsError,
    QuerySyntaxError,
    SemanticError,
    TransactionAbortedError,
)
from common.interfaces import (
    ConcurrencyControlManager,
    FailureRecoveryManager,
    QueryOptimizer,
    QueryProcessor,
    Session,
    StorageManager,
)
from common.log import RecoveryTarget
from common.plan import ParsedQuery, QueryKind, TransactionCommand
from common.result import ExecutionResult
from common.transaction import Action
from query_processor.context import ExecContext
from query_processor.errors import PlanError
from query_processor.result import ResultFormatter
from query_processor.session import SessionManager
from query_processor.transactions import TransactionCoordinator
from query_processor.translator import PlanTranslator

logger = logging.getLogger("coachdb.qp")

_EXPLAIN_ALLOWED_KINDS = {
    QueryKind.EXPLAIN,
    QueryKind.SELECT,
    QueryKind.UPDATE,
    QueryKind.DELETE,
}

_SHOW_LOG_RE = re.compile(r"^SHOW\s+LOG(?:\s+(\S+))?$", re.IGNORECASE)
_RESTORE_TX_RE = re.compile(r"^RESTORE\s+TO\s+TX\s+(\S+)$", re.IGNORECASE)
_RESTORE_TIME_RE = re.compile(
    r"^RESTORE\s+TO\s+TIME\s+(?:'([^']*)'|\"([^\"]*)\"|(\S+))$",
    re.IGNORECASE,
)


class MetaCommandHandler:
    def __init__(
        self,
        qo: QueryOptimizer,
        frm: FailureRecoveryManager | None = None,
        ccm: ConcurrencyControlManager | None = None,
    ) -> None:
        self._qo = qo
        self._frm = frm
        self._ccm = ccm

    def explain(self, parsed: ParsedQuery | str) -> ExecutionResult:
        if isinstance(parsed, str):
            raw = parsed.strip().rstrip(";").strip()
            inner_sql = raw
            if raw.upper().startswith("EXPLAIN"):
                inner_sql = raw[len("EXPLAIN") :].strip()
            if not inner_sql:
                raise QuerySyntaxError("Query setelah EXPLAIN tidak boleh kosong", position=0, token="EXPLAIN")

            try:
                pq = self._qo.parse_query(raw)
            except QuerySyntaxError:
                pq = self._qo.parse_query(inner_sql)
        else:
            pq = parsed

        if pq.kind not in _EXPLAIN_ALLOWED_KINDS:
            raise QuerySyntaxError(
                "EXPLAIN hanya didukung untuk perintah SELECT, UPDATE, atau DELETE",
                position=0,
                token=pq.kind.value,
            )

        text = self._qo.explain(pq)
        return ExecutionResult(ok=True, message=text)

    def checkpoint(self, session: Session) -> ExecutionResult:
        self.require_outside_tx(session)
        if self._frm is None:
            return ExecutionResult(ok=False, message="ERROR [INTERNAL]: Failure Recovery Manager tidak tersedia")

        res = self._frm.checkpoint()
        if not res.ok:
            return ExecutionResult(ok=False, message=res.message or "ERROR [LOG]: CHECKPOINT gagal")

        msg = res.message or (
            f"CHECKPOINT selesai (lsn {res.lsn})" if res.lsn is not None else "CHECKPOINT selesai"
        )
        return ExecutionResult(ok=True, message=msg)

    def show_log(self, tx_id: int | None = None) -> ExecutionResult:
        if self._frm is None:
            return ExecutionResult(ok=False, message="ERROR [INTERNAL]: Failure Recovery Manager tidak tersedia")

        records = self._frm.read_log(tx_id=tx_id)
        columns = ["lsn", "tx_id", "kind", "change", "timestamp"]
        rows: list[list[object]] = []
        for rec in records:
            change_repr = None
            if rec.change is not None:
                change_repr = (
                    f"{rec.change.op.value} {rec.change.table} "
                    f"rid=({rec.change.rid.page_id},{rec.change.rid.slot})"
                )
            rows.append([rec.lsn, rec.tx_id, rec.kind.value, change_repr, rec.timestamp])

        return ExecutionResult(ok=True, columns=columns, rows=rows)

    def restore(self, target: RecoveryTarget, session: Session) -> ExecutionResult:
        self.require_outside_tx(session)
        if self._frm is None:
            return ExecutionResult(ok=False, message="ERROR [INTERNAL]: Failure Recovery Manager tidak tersedia")

        res = self._frm.recover_to(target)
        if not res.ok:
            return ExecutionResult(ok=False, message=res.message or "ERROR [RECOVERY]: RESTORE gagal")

        if self._ccm is not None:
            self._ccm.initialize(res.last_tx_id)

        msg = res.message or f"RESTORE selesai (last_tx_id={res.last_tx_id}, last_lsn={res.last_lsn})"
        return ExecutionResult(ok=True, message=msg)

    def require_outside_tx(self, session: Session) -> None:
        if session.in_explicit_tx or session.tx_id is not None:
            raise MdbmsError("ERROR [TX]: Perintah ini tidak diizinkan di dalam transaksi aktif")


class QueryProcessorFacade(QueryProcessor):
    def __init__(
        self,
        qo: QueryOptimizer,
        sm: StorageManager,
        ccm: ConcurrencyControlManager,
        frm: FailureRecoveryManager | None = None,
        translator: PlanTranslator | None = None,
        transactions: TransactionCoordinator | None = None,
        meta: MetaCommandHandler | None = None,
        sessions: SessionManager | None = None,
    ) -> None:
        self._qo = qo
        self._sm = sm
        self._ccm = ccm
        self._frm = frm
        self._translator = translator or PlanTranslator()
        self._transactions = transactions or TransactionCoordinator(ccm)
        self._meta = meta or MetaCommandHandler(qo=qo, frm=frm, ccm=ccm)
        self._sessions = sessions or SessionManager()
        self._formatter = ResultFormatter()
        self._closing = False
        self._storage_closed = False
        self._closed = False

    def open_session(self) -> Session:
        if self._closing:
            raise MdbmsError("Query Processor sudah ditutup")
        return self._sessions.open_session()

    def close(self) -> None:
        """Tutup runtime setelah seluruh request server berhenti."""
        if self._closed:
            return
        self._closing = True
        for session in self._sessions.active_sessions():
            self.close_session(session.session_id)
        if self._sessions.active_sessions():
            raise MdbmsError("Transaksi belum selesai; runtime belum boleh ditutup")
        if not self._storage_closed:
            self._sm.flush()
            close_storage = getattr(self._sm, "close", None)
            if callable(close_storage):
                close_storage()
            self._storage_closed = True
        close_recovery = getattr(self._frm, "close", None)
        if callable(close_recovery):
            close_recovery()
        self._closed = True

    def close_session(self, session_id: int) -> None:
        session = self._sessions.get_session(session_id)
        if session is not None and (session.tx_id is not None or session.in_explicit_tx):
            tx_id = session.tx_id
            try:
                self._transactions.abort_on_disconnect(session)
            except Exception:
                logger.exception(
                    "Gagal membatalkan transaksi aktif %s pada sesi %s", tx_id, session_id
                )
                return
        self._sessions.close_session(session_id)

    def execute_query(self, sql: str, session: Session) -> ExecutionResult:
        if self._closing:
            return ExecutionResult(ok=False, message="ERROR: Query Processor sudah ditutup")
        if session is not None:
            self._sessions.register_session(session)
            allow_abort = isinstance(sql, str) and sql.strip().rstrip(";").strip().upper() == "ABORT"
            blocked = self._transactions.blocked_result(session, allow_abort=allow_abort)
            if blocked is not None:
                return blocked

        if not isinstance(sql, str):
            return ExecutionResult(ok=False, message="ERROR [SYNTAX]: Query harus berupa string")

        cleaned = sql.strip().rstrip(";").strip()
        if not cleaned:
            return ExecutionResult(ok=False, message="ERROR [SYNTAX]: Query kosong")

        try:
            meta_result = self._try_handle_meta_command(cleaned, sql, session)
            if meta_result is not None:
                return meta_result

            parsed = self._qo.parse_query(sql)

            if parsed.kind == QueryKind.EXPLAIN:
                return self._meta.explain(parsed)

            if parsed.kind == QueryKind.TRANSACTION:
                return self._handle_transaction(parsed.command, session)

            return self._execute_data_or_index_query(parsed, session)

        except (QuerySyntaxError, SemanticError) as exc:
            return ExecutionResult(ok=False, message=self._formatter.format_error(exc))
        except TransactionAbortedError as exc:
            return self._runtime_error_result(session, exc)
        except MdbmsError as exc:
            if isinstance(exc, PlanError):
                return ExecutionResult(ok=False, message=self._formatter.format_error(exc))
            return self._runtime_error_result(session, exc)
        except Exception as exc:
            logger.exception("Kesalahan internal saat mengeksekusi query: %s", sql)
            return self._runtime_error_result(session, exc)

    def execute(self, session: Session, sql: str) -> ExecutionResult:
        return self.execute_query(sql, session)

    def _try_handle_meta_command(
        self,
        cleaned: str,
        raw_sql: str,
        session: Session,
    ) -> ExecutionResult | None:
        upper = cleaned.upper()

        if upper == "EXPLAIN" or upper.startswith("EXPLAIN "):
            return self._meta.explain(raw_sql)

        if upper == "CHECKPOINT":
            return self._meta.checkpoint(session)

        if upper == "SHOW LOG" or upper.startswith("SHOW LOG "):
            match = _SHOW_LOG_RE.match(cleaned)
            if not match:
                raise QuerySyntaxError("Sintaks SHOW LOG tidak valid", position=0, token="SHOW")
            tx_arg = match.group(1)
            tx_id: int | None = None
            if tx_arg is not None:
                try:
                    tx_id = int(tx_arg)
                except ValueError as exc:
                    raise QuerySyntaxError(
                        f"tx_id pada SHOW LOG harus bilangan bulat: '{tx_arg}'",
                        position=0,
                        token=tx_arg,
                    ) from exc
            return self._meta.show_log(tx_id=tx_id)

        if upper.startswith("RESTORE "):
            tx_match = _RESTORE_TX_RE.match(cleaned)
            if tx_match:
                tx_str = tx_match.group(1)
                try:
                    target_tx = int(tx_str)
                except ValueError as exc:
                    raise QuerySyntaxError(
                        f"Target transaksi RESTORE tidak valid: '{tx_str}'",
                        position=0,
                        token=tx_str,
                    ) from exc
                return self._meta.restore(RecoveryTarget(tx_id=target_tx), session)

            time_match = _RESTORE_TIME_RE.match(cleaned)
            if time_match:
                time_str = time_match.group(1) or time_match.group(2) or time_match.group(3) or ""
                ts = self._parse_restore_timestamp(time_str)
                return self._meta.restore(RecoveryTarget(timestamp=ts), session)

            raise QuerySyntaxError(
                "Sintaks RESTORE tidak valid; gunakan RESTORE TO TX <n> atau RESTORE TO TIME '<waktu>'",
                position=0,
                token="RESTORE",
            )

        return None

    @staticmethod
    def _parse_restore_timestamp(time_str: str) -> float:
        cleaned = time_str.strip()
        if not cleaned:
            raise QuerySyntaxError("Waktu RESTORE TO TIME tidak boleh kosong", position=0, token="TIME")
        try:
            return float(cleaned)
        except ValueError:
            pass
        try:
            dt = datetime.fromisoformat(cleaned)
            return dt.timestamp()
        except ValueError as exc:
            raise QuerySyntaxError(
                f"Format waktu RESTORE tidak valid: '{cleaned}'",
                position=0,
                token=cleaned,
            ) from exc

    def _handle_transaction(
        self,
        command: TransactionCommand | None,
        session: Session,
    ) -> ExecutionResult:
        if command == TransactionCommand.BEGIN:
            return self._transactions.begin(session)
        if command == TransactionCommand.COMMIT:
            return self._transactions.commit(session)
        if command == TransactionCommand.ABORT:
            return self._transactions.abort(session)

        return ExecutionResult(ok=False, message="ERROR [TX]: Perintah transaksi tidak dikenal")

    def _execute_data_or_index_query(
        self,
        parsed: ParsedQuery,
        session: Session,
    ) -> ExecutionResult:
        if parsed.kind == QueryKind.CREATE_INDEX and (
            session.in_explicit_tx or session.tx_id is not None
        ):
            return ExecutionResult(
                ok=False,
                message="ERROR [TX]: CREATE INDEX tidak diizinkan di dalam transaksi eksplisit",
            )

        optimized = self._qo.optimize_query(parsed)
        if optimized.plan is not None:
            self._translator.validate(optimized.plan)

        intent = (
            Action.READ
            if optimized.kind in (QueryKind.SELECT, QueryKind.CREATE_INDEX)
            else Action.WRITE
        )

        def _work(tx_id: int) -> ExecutionResult:
            ctx = ExecContext(
                tx_id=tx_id,
                sm=self._sm,
                ccm=self._ccm,
                access_intent=intent,
            )
            ctx.check_cancelled()
            executable = self._translator.build(optimized, ctx)
            res = executable.execute(ctx)
            ctx.check_cancelled()
            if res.ok and not res.message:
                res.message = self._default_success_message(optimized.kind, res.affected_rows)
            return res

        implicit_tx = (not session.in_explicit_tx) and (session.tx_id is None)
        if not implicit_tx:
            assert session.tx_id is not None
            res = _work(session.tx_id)
            if not res.ok and not res.message:
                return ExecutionResult(ok=False, message="ERROR: Eksekusi operator belum diimplementasikan")
            return res

        return self._transactions.run_autocommit(session, _work)

    @staticmethod
    def _default_success_message(kind: QueryKind, affected_rows: int) -> str:
        if kind == QueryKind.INSERT:
            return f"{affected_rows} baris ditambahkan"
        if kind == QueryKind.UPDATE:
            return f"{affected_rows} baris diubah"
        if kind == QueryKind.DELETE:
            return f"{affected_rows} baris dihapus"
        if kind == QueryKind.CREATE_INDEX:
            return "Index berhasil dibuat"
        return ""

    def _runtime_error_result(self, session: Session, error: Exception) -> ExecutionResult:
        result = ExecutionResult(ok=False, message=self._formatter.format_error(error))
        try:
            if session is not None:
                self._transactions.handle_runtime_error(session, error)
        except Exception as cleanup_error:
            result.message += f"; cleanup gagal: {cleanup_error}"
        return result
