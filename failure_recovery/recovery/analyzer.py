"""Fase analisis log saat recovery."""

from __future__ import annotations

from common.log import LogKind, LogRecord
from failure_recovery.ports import LogReadInterface
from failure_recovery.recovery.analysis import AnalysisResult, TransactionState


class RecoveryAnalyzer:
    """Membangun peta status transaksi dan daftar perubahan dari log."""

    def __init__(self, log_reader: LogReadInterface) -> None:
        self._log_reader = log_reader

    def analyze(self, start_lsn: int, seed_active) -> AnalysisResult | None:
        records = self._log_reader.forward_scan(start_lsn)
        tx_state = {tx_id: TransactionState.ACTIVE for tx_id in seed_active}
        changes: list[LogRecord] = []
        max_tx_id = max(tx_state, default=0)
        last_lsn = start_lsn

        for record in records:
            last_lsn = max(last_lsn, record.lsn)
            if record.kind is LogKind.DATA:
                changes.append(record)

            tx_id = record.tx_id
            if tx_id is None:
                continue

            max_tx_id = max(max_tx_id, tx_id)
            if record.kind is LogKind.COMMIT:
                tx_state[tx_id] = TransactionState.COMMITTED
            elif record.kind is LogKind.ABORT:
                tx_state[tx_id] = TransactionState.ABORTED
            elif tx_id not in tx_state:
                tx_state[tx_id] = TransactionState.ACTIVE

        losers = sorted(
            tx_id
            for tx_id, state in tx_state.items()
            if state is TransactionState.ACTIVE
        )
        return AnalysisResult(
            start_lsn=start_lsn,
            last_lsn=last_lsn,
            max_tx_id=max_tx_id,
            tx_state=tx_state,
            changes=changes,
            losers=losers,
        )
