from __future__ import annotations

from typing import TYPE_CHECKING

from common.interfaces import (
    ConcurrencyControlManager,
    FailureRecoveryManager,
    QueryOptimizer,
    QueryProcessor,
    Session,
    StorageManager,
)
from common.log import RecoveryTarget
from common.plan import ParsedQuery
from common.result import ExecutionResult
from query_processor.session import SessionManager

if TYPE_CHECKING:
    from query_processor.transactions import TransactionCoordinator
    from query_processor.translator import PlanTranslator


class MetaCommandHandler:
    def __init__(
        self,
        qo: QueryOptimizer,
        frm: FailureRecoveryManager | None = None,
    ) -> None:
        self._qo = qo
        self._frm = frm

    def explain(self, parsed: ParsedQuery | str) -> ExecutionResult:
        return ExecutionResult(ok=False)

    def checkpoint(self, session: Session) -> ExecutionResult:
        return ExecutionResult(ok=False)

    def show_log(self, tx_id: int | None = None) -> ExecutionResult:
        return ExecutionResult(ok=False)

    def restore(self, target: RecoveryTarget, session: Session) -> ExecutionResult:
        return ExecutionResult(ok=False)

    def require_outside_tx(self, session: Session) -> None:
        return None


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
        self._translator = translator
        self._transactions = transactions
        self._meta = meta or MetaCommandHandler(qo=qo, frm=frm)
        self._sessions = sessions or SessionManager()

    def open_session(self) -> Session:
        return Session(session_id=0)

    def close_session(self, session_id: int) -> None:
        return None

    def execute_query(self, sql: str, session: Session) -> ExecutionResult:
        return ExecutionResult(ok=False)

    def execute(self, session: Session, sql: str) -> ExecutionResult:
        return self.execute_query(sql, session)
