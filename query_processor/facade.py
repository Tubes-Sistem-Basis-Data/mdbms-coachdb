"""Facade utama Query Processor dan handler perintah meta (QP-01 & QP-02)."""

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
    """Menangani perintah meta: EXPLAIN, CHECKPOINT, SHOW LOG, dan RESTORE TO."""

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
        """Jalankan EXPLAIN pada query SELECT/UPDATE/DELETE tanpa mengeksekusi plan."""
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
        """Jalankan CHECKPOINT di luar transaksi aktif."""
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
        """Tampilkan catatan WAL dari Failure Recovery Manager."""
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
        """Pulihkan basis data ke target waktu atau transaksi (PITR) di luar transaksi aktif."""
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
        """Pastikan sesi tidak sedang berada di dalam transaksi eksplisit."""
        if session.in_explicit_tx or session.tx_id is not None:
            raise MdbmsError("ERROR [TX]: Perintah ini tidak diizinkan di dalam transaksi aktif")


class QueryProcessorFacade(QueryProcessor):
    """Orkestrator utama eksekusi query CoachDB (implementasi kontrak QueryProcessor)."""

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

    def open_session(self) -> Session:
        """Buka sesi baru untuk satu client."""
        return self._sessions.open_session()

    def close_session(self, session_id: int) -> None:
        """Tutup sesi dan batalkan transaksi yang masih aktif pada sesi tersebut."""
        session = self._sessions.close_session(session_id)
        if session is not None and (session.tx_id is not None or session.in_explicit_tx):
            self._abort_active_tx(session, is_disconnect=True)

    def execute_query(self, sql: str, session: Session) -> ExecutionResult:
        """Jalankan satu perintah SQL dalam konteks sesi tanpa melempar exception keluar."""
        if session is not None:
            self._sessions.register_session(session)

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
            if session is not None:
                session.tx_id = None
                session.in_explicit_tx = False
            return ExecutionResult(ok=False, message=self._formatter.format_error(exc))
        except MdbmsError as exc:
            if exc.__class__.__name__ != "PlanError" and session is not None:
                if session.tx_id is not None or session.in_explicit_tx:
                    self._abort_active_tx(session)
            return ExecutionResult(ok=False, message=self._formatter.format_error(exc))
        except Exception as exc:
            logger.exception("Kesalahan internal saat mengeksekusi query: %s", sql)
            if session is not None and (session.tx_id is not None or session.in_explicit_tx):
                self._abort_active_tx(session)
            return ExecutionResult(ok=False, message=self._formatter.format_error(exc))

    def execute(self, session: Session, sql: str) -> ExecutionResult:
        """Alias kompatibilitas diagram kelas (+execute(session, sql): QueryResult)."""
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
            if session.in_explicit_tx or session.tx_id is not None:
                return ExecutionResult(
                    ok=False,
                    message=f"ERROR [TX]: Transaksi {session.tx_id} sudah aktif pada sesi ini",
                )
            coord_res = self._transactions.begin(session)
            if coord_res.ok or coord_res.message:
                return coord_res

            tx_id = self._ccm.begin_transaction()
            session.tx_id = tx_id
            session.in_explicit_tx = True
            return ExecutionResult(ok=True, message=f"BEGIN (tx {tx_id})")

        if command == TransactionCommand.COMMIT:
            if not session.in_explicit_tx or session.tx_id is None:
                return ExecutionResult(
                    ok=False,
                    message="ERROR [TX]: Tidak ada transaksi aktif untuk di-COMMIT",
                )
            coord_res = self._transactions.commit(session)
            if coord_res.ok or coord_res.message:
                return coord_res

            tx_id = session.tx_id
            tx_result = self._ccm.commit(tx_id)
            session.tx_id = None
            session.in_explicit_tx = False
            if not tx_result.ok:
                return ExecutionResult(
                    ok=False,
                    message=tx_result.message or f"ERROR [TX]: Gagal melakukan COMMIT untuk tx {tx_id}",
                )
            return ExecutionResult(ok=True, message=tx_result.message or f"COMMIT (tx {tx_id})")

        if command == TransactionCommand.ABORT:
            if not session.in_explicit_tx or session.tx_id is None:
                return ExecutionResult(
                    ok=False,
                    message="ERROR [TX]: Tidak ada transaksi aktif untuk di-ABORT",
                )
            coord_res = self._transactions.abort(session)
            if coord_res.ok or coord_res.message:
                return coord_res

            tx_id = session.tx_id
            tx_result = self._ccm.abort(tx_id)
            session.tx_id = None
            session.in_explicit_tx = False
            return ExecutionResult(ok=True, message=tx_result.message or f"ABORT (tx {tx_id})")

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
            executable = self._translator.build(optimized, ctx)
            res = executable.execute(ctx)
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

        invoked = False

        def _tracked_work(tx_id: int) -> ExecutionResult:
            nonlocal invoked
            invoked = True
            return _work(tx_id)

        coord_res = self._transactions.run_autocommit(session, _tracked_work)
        if invoked or coord_res.ok or coord_res.message:
            return coord_res

        tx_id = self._ccm.begin_transaction()
        session.tx_id = tx_id
        session.in_explicit_tx = False
        try:
            res = _work(tx_id)
        except Exception:
            self._abort_active_tx(session)
            raise

        if not res.ok:
            self._ccm.abort(tx_id)
            session.tx_id = None
            session.in_explicit_tx = False
            return ExecutionResult(
                ok=False,
                message=res.message or "ERROR: Eksekusi operator belum diimplementasikan",
            )

        commit_res = self._ccm.commit(tx_id)
        session.tx_id = None
        session.in_explicit_tx = False
        if not commit_res.ok:
            return ExecutionResult(
                ok=False,
                message=commit_res.message or f"ERROR [TX]: Gagal commit transaksi {tx_id}",
            )
        return res

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

    def _abort_active_tx(self, session: Session, is_disconnect: bool = False) -> None:
        tx_id = session.tx_id
        try:
            if is_disconnect:
                self._transactions.abort_on_disconnect(session)
            if session.tx_id is not None:
                self._ccm.abort(session.tx_id)
            elif tx_id is not None:
                self._ccm.abort(tx_id)
        except Exception:
            logger.exception("Gagal membatalkan transaksi aktif %s pada sesi %s", tx_id, session.session_id)
        finally:
            session.tx_id = None
            session.in_explicit_tx = False
