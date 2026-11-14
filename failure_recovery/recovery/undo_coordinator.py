"""Koordinasi fase undo saat recovery."""

from __future__ import annotations

from failure_recovery.ports import RollbackInterface
from failure_recovery.recovery.analysis import AnalysisResult
from failure_recovery.results import RollbackResult


class UndoCoordinator:
    """Membatalkan seluruh transaksi loser lewat RollbackInterface."""

    def __init__(self, rollback: RollbackInterface) -> None:
        self._rollback = rollback

    def run(self, analysis: AnalysisResult) -> tuple[list[int], int]:
        changes_by_tx: dict[int, int] = {}
        last_lsn_by_tx: dict[int, int] = {}
        for record in analysis.changes:
            if record.tx_id is None:
                continue
            changes_by_tx[record.tx_id] = changes_by_tx.get(record.tx_id, 0) + 1
            last_lsn_by_tx[record.tx_id] = max(
                last_lsn_by_tx.get(record.tx_id, 0), record.lsn
            )

        undone: list[int] = []
        undo_count = 0
        for tx_id in sorted(
            analysis.losers, key=lambda item: last_lsn_by_tx.get(item, 0), reverse=True
        ):
            result = self._rollback.rollback(tx_id)
            if result in (RollbackResult.DONE, RollbackResult.ALREADY_ABORT):
                undone.append(tx_id)
                undo_count += changes_by_tx.get(tx_id, 0)
        return undone, undo_count
