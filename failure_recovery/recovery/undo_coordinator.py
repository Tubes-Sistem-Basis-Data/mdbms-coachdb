"""Koordinasi fase undo saat recovery."""

from __future__ import annotations

from failure_recovery.ports import RollbackInterface
from failure_recovery.recovery.analysis import AnalysisResult


class UndoCoordinator:
    """Membatalkan seluruh transaksi loser lewat RollbackInterface."""

    def __init__(self, rollback: RollbackInterface) -> None:
        self._rollback = rollback

    def run(self, analysis: AnalysisResult) -> tuple[list[int], int]:
        return ([], 0)
