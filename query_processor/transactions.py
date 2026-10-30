from __future__ import annotations

from typing import Callable

from common.interfaces import ConcurrencyControlManager, Session
from common.result import ExecutionResult


class TransactionCoordinator:
    """Mengelola transaksi"""

    def __init__(self, ccm: ConcurrencyControlManager) -> None:
        self._ccm = ccm

    def begin(self, session: Session) -> ExecutionResult:
        raise NotImplementedError("QP-12: TransactionCoordinator.begin belum diimplementasikan")

    def commit(self, session: Session) -> ExecutionResult:
        raise NotImplementedError("QP-12: TransactionCoordinator.commit belum diimplementasikan")

    def abort(self, session: Session) -> ExecutionResult:
        raise NotImplementedError("QP-12: TransactionCoordinator.abort belum diimplementasikan")

    def run_autocommit(self, session: Session, work: Callable[[int], ExecutionResult]) -> ExecutionResult:
        raise NotImplementedError("QP-12: TransactionCoordinator.run_autocommit belum diimplementasikan")

    def abort_on_disconnect(self, session: Session) -> None:
        raise NotImplementedError("QP-12: TransactionCoordinator.abort_on_disconnect belum diimplementasikan")

    def handle_runtime_error(self, session: Session, error: Exception) -> None:
        raise NotImplementedError("QP-12: TransactionCoordinator.handle_runtime_error belum diimplementasikan")
