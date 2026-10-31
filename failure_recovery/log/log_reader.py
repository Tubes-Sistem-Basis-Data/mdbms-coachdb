"""Pembaca isi Write-Ahead Log."""

from __future__ import annotations

from common.log import LogRecord
from failure_recovery.ports import LogReadInterface


class LogReader(LogReadInterface):
    """Pembaca WAL berbasis file JSON per baris."""

    def read_log(self, filter_tx: int) -> list[LogRecord]:
        return []

    def forward_scan(self, from_lsn: int) -> list[LogRecord]:
        return []

    def backward_scan(self, tx_id: int) -> list[LogRecord]:
        return []

    # nanti ubah None-nya
    def _parse_record(self, data: bytes) -> LogRecord | None:
        return None

    def _read_all(self) -> list[LogRecord]:
        return []
