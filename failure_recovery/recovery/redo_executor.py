"""Fase redo saat recovery."""

from __future__ import annotations

from collections.abc import Iterable

from common.log import LogRecord
from common.transaction import ChangeOp
from failure_recovery.recovery.analysis import AnalysisResult, TransactionState


class RedoExecutor:
    """Menerapkan ulang after-image transaksi yang sudah commit."""

    def __init__(self, sm) -> None:
        self._sm = sm

    def run(self, analysis: AnalysisResult) -> int:
        return self.replay(
            record for record in analysis.changes
            if record.tx_id is not None
            and analysis.tx_state.get(record.tx_id) is TransactionState.COMMITTED
        )

    def replay(self, records: Iterable[LogRecord]) -> int:
        """Terapkan perubahan yang sudah dipilih, sesuai urutan LSN."""
        redo_count = 0
        for record in sorted(records, key=lambda item: item.lsn):
            change = record.change
            if change is None:
                continue

            if change.op is ChangeOp.INSERT:
                self._sm.insert(change.table, change.rid, change.after, None)
            elif change.op is ChangeOp.UPDATE:
                self._sm.update(change.table, change.rid, change.after, None)
            elif change.op is ChangeOp.DELETE:
                self._sm.delete(change.table, change.rid, None)
            else:
                continue
            redo_count += 1
        return redo_count
