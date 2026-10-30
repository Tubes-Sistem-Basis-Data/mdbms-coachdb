"""Mock Query Processor: alur eksekusi nyata untuk SELECT * satu tabel dan transaksi."""

from __future__ import annotations

import threading
import time

from common import config
from common.errors import MdbmsError, TransactionAbortedError
from common.interfaces import (
    ConcurrencyControlManager,
    QueryOptimizer,
    QueryProcessor,
    Session,
    StorageManager,
)
from common.plan import ParsedQuery, PlanNode, Project, QueryKind, TableScan, TransactionCommand
from common.result import ExecutionResult
from common.transaction import AccessStatus, Action, ResourceId


class MockQueryProcessor(QueryProcessor):
    """QP mock dengan facade QO, SM, dan CCM yang disuntikkan."""

    def __init__(self, qo: QueryOptimizer, sm: StorageManager, ccm: ConcurrencyControlManager) -> None:
        self._qo = qo
        self._sm = sm
        self._ccm = ccm
        self._lock = threading.Lock()
        self._sessions: dict[int, Session] = {}
        self._next_session = 1

    def open_session(self) -> Session:
        """Buka sesi baru."""
        with self._lock:
            session = Session(self._next_session)
            self._sessions[session.session_id] = session
            self._next_session += 1
        return session

    def close_session(self, session_id: int) -> None:
        """Tutup sesi, abort transaksi yang masih aktif."""
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is not None and session.tx_id is not None:
            self._ccm.abort(session.tx_id)

    def execute_query(self, sql: str, session: Session) -> ExecutionResult:
        """Parse lewat QO lalu jalankan. MdbmsError dikembalikan sebagai ok=False."""
        auto_tx: int | None = None
        try:
            parsed = self._qo.parse_query(sql)
            match parsed.kind:
                case QueryKind.TRANSACTION:
                    return self._transaction(parsed, session)
                case QueryKind.EXPLAIN:
                    return ExecutionResult(ok=True, message=self._qo.explain(parsed))
                case QueryKind.SELECT:
                    if session.tx_id is None:
                        auto_tx = self._ccm.begin_transaction()
                    tx_id = session.tx_id if session.tx_id is not None else auto_tx
                    result = self._select(self._qo.optimize_query(parsed), tx_id)
                    if auto_tx is not None:
                        self._commit(auto_tx)
                        auto_tx = None
                    return result
                case _:
                    raise MdbmsError(f"mock: {parsed.kind.value} belum didukung")
        except TransactionAbortedError as e:
            if session.tx_id == e.tx_id:
                session.tx_id = None
                session.in_explicit_tx = False
            return ExecutionResult(ok=False, message=e.message)
        except MdbmsError as e:
            if auto_tx is not None:
                self._ccm.abort(auto_tx)
            return ExecutionResult(ok=False, message=e.message)

    def _transaction(self, parsed: ParsedQuery, session: Session) -> ExecutionResult:
        match parsed.command:
            case TransactionCommand.BEGIN:
                if session.in_explicit_tx:
                    raise MdbmsError("transaksi sudah berjalan")
                session.tx_id = self._ccm.begin_transaction()
                session.in_explicit_tx = True
                return ExecutionResult(ok=True, message=f"BEGIN (tx {session.tx_id})")
            case TransactionCommand.COMMIT | TransactionCommand.ABORT:
                if not session.in_explicit_tx or session.tx_id is None:
                    raise MdbmsError("tidak ada transaksi aktif")
                tx_id = session.tx_id
                session.tx_id = None
                session.in_explicit_tx = False
                if parsed.command is TransactionCommand.COMMIT:
                    res = self._ccm.commit(tx_id)
                else:
                    res = self._ccm.abort(tx_id)
                return ExecutionResult(ok=res.ok, message=f"{parsed.command.value} (tx {tx_id}) {res.message}".strip())
        raise MdbmsError("perintah transaksi tidak dikenal")

    def _commit(self, tx_id: int) -> None:
        res = self._ccm.commit(tx_id)
        if not res.ok:
            raise TransactionAbortedError(res.message or "commit gagal", tx_id)

    def _request(self, tx_id: int, resource: ResourceId, action: Action) -> None:
        while True:
            resp = self._ccm.validate_object(tx_id, resource, action)
            if resp.status is AccessStatus.ALLOWED:
                return
            if resp.status is AccessStatus.ABORTED:
                raise TransactionAbortedError(resp.message or f"transaksi {tx_id} dibatalkan", tx_id)
            time.sleep(config.WAIT_RETRY_SECONDS)

    def _select(self, parsed: ParsedQuery, tx_id: int) -> ExecutionResult:
        plan = parsed.plan
        if not isinstance(plan, Project) or len(plan.children) != 1 or not isinstance(plan.children[0], TableScan):
            raise MdbmsError("mock: hanya Project(TableScan) yang didukung")
        rows = self._scan(plan.children[0], tx_id)
        schema = self._sm.get_schema(plan.children[0].table)
        idx = [schema.column_index(c.column) for c in plan.columns]
        out = [[r[i] for i in idx] for r in rows]
        return ExecutionResult(
            ok=True,
            columns=[c.column for c in plan.columns],
            rows=out,
            message=f"{len(out)} baris",
        )

    def _scan(self, node: PlanNode, tx_id: int) -> list[list]:
        assert isinstance(node, TableScan)
        self._request(tx_id, ResourceId(node.table), Action.READ)
        return [row.values for row in self._sm.scan(node.table)]
