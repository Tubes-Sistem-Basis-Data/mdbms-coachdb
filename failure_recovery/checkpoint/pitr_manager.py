"""Point-in-time recovery ke waktu atau transaksi tertentu."""

from __future__ import annotations

from datetime import datetime

from common.log import LogRecord, RecoveryTarget
from failure_recovery.results import RestoreResult
from failure_recovery.ports import CheckpointQueryInterface, LogReadInterface, LogWriter


class PITRManager:
    """Pemulihan historis ke waktu atau transaksi tertentu."""

    def __init__(
        self,
        store: CheckpointQueryInterface,
        sm,
        log_writer: LogWriter,
        log_reader: LogReadInterface,
    ) -> None:
        self._store = store
        self._sm = sm
        self._log_writer = log_writer
        self._log_reader = log_reader

    def restore_to_time(
        self,
        target_time: datetime,
        target_tx_id: int | None = None,
    ) -> RestoreResult | None:
        return None

    def restore_to_tx(self, target_tx_id: int) -> RestoreResult | None:
        return None

    def _restore(self, target: RecoveryTarget) -> RestoreResult | None:
        return None

    def _redo(self, records: list[LogRecord]) -> None:
        return None
