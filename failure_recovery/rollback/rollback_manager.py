"""Rollback transaksi lewat Storage Manager."""

from __future__ import annotations

from common.log import LogRecord
from failure_recovery.results import RollbackResult
from failure_recovery.ports import (
    LogFlusher,
    LogReadInterface,
    LogWriter,
    RollbackInterface,
)


class RollbackManager(RollbackInterface):
    """Membatalkan transaksi dengan undo idempoten dan menulis CLR."""

    def __init__(
        self,
        log_writer: LogWriter,
        log_flusher: LogFlusher,
        log_reader: LogReadInterface,
        sm,
    ) -> None:
        self._log_writer = log_writer
        self._log_flusher = log_flusher
        self._log_reader = log_reader
        self._sm = sm

    def rollback(self, tx_id: int) -> RollbackResult | None:
        return None

    def _undo_record(self, record: LogRecord) -> None:
        return None

    def _is_already_aborted(self, tx_id: int) -> bool:
        return False
