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
        raise NotImplementedError("QP-02: MetaCommandHandler.explain belum diimplementasikan")

    def checkpoint(self, session: Session) -> ExecutionResult:
        raise NotImplementedError("QP-02: MetaCommandHandler.checkpoint belum diimplementasikan")

    def show_log(self, tx_id: int | None = None) -> ExecutionResult:
        raise NotImplementedError("QP-02: MetaCommandHandler.show_log belum diimplementasikan")

    def restore(self, target: RecoveryTarget, session: Session) -> ExecutionResult:
        raise NotImplementedError("QP-02: MetaCommandHandler.restore belum diimplementasikan")

    def require_outside_tx(self, session: Session) -> None:
        raise NotImplementedError(
            "QP-02: MetaCommandHandler.require_outside_tx belum diimplementasikan"
        )


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
        raise NotImplementedError("QP-02: QueryProcessorFacade.open_session belum diimplementasikan")

    def close_session(self, session_id: int) -> None:
        raise NotImplementedError(
            "QP-02: QueryProcessorFacade.close_session belum diimplementasikan"
        )

    def execute_query(self, sql: str, session: Session) -> ExecutionResult:
        raise NotImplementedError(
            "QP-02: QueryProcessorFacade.execute_query belum diimplementasikan"
        )

    def execute(self, session: Session, sql: str) -> ExecutionResult:
        return self.execute_query(sql, session)
