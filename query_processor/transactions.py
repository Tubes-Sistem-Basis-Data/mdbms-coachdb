from __future__ import annotations

from typing import Callable

from common.interfaces import ConcurrencyControlManager, Session
from common.result import ExecutionResult


class TransactionCoordinator:
    """Mengelola transaksi"""

    def __init__(self, ccm: ConcurrencyControlManager) -> None:
        self._ccm = ccm

    def begin(self, session: Session) -> ExecutionResult:
        return ExecutionResult(ok=False)

    def commit(self, session: Session) -> ExecutionResult:
        return ExecutionResult(ok=False)

    def abort(self, session: Session) -> ExecutionResult:
        return ExecutionResult(ok=False)

    def run_autocommit(self, session: Session, work: Callable[[int], ExecutionResult]) -> ExecutionResult:
        return ExecutionResult(ok=False)

    def abort_on_disconnect(self, session: Session) -> None:
        return None

    def handle_runtime_error(self, session: Session, error: Exception) -> None:
        return None
