"""Pembaca isi Write-Ahead Log."""

from __future__ import annotations

from pathlib import Path

from common.log import LogRecord
from failure_recovery.log import codec
from failure_recovery.ports import LogReadInterface


class LogReader(LogReadInterface):
    """Pembaca WAL berbasis file JSON per baris."""

    def __init__(self, log_path: str | Path | None = None) -> None:
        self._log_path = Path(log_path) if log_path is not None else None

    def read_log(self, filter_tx: int) -> list[LogRecord]:
        records = self._read_all()
        if filter_tx < 0:
            return records
        return [record for record in records if record.tx_id == filter_tx]

    def forward_scan(self, from_lsn: int) -> list[LogRecord]:
        records = [record for record in self._read_all() if record.lsn >= from_lsn]
        records.sort(key=lambda record: record.lsn)
        return records

    def backward_scan(self, tx_id: int) -> list[LogRecord]:
        records = [record for record in self._read_all() if record.tx_id == tx_id]
        records.sort(key=lambda record: record.lsn, reverse=True)
        return records

    def _parse_record(self, data: bytes) -> LogRecord | None:
        return codec.parse(data)

    def _read_all(self) -> list[LogRecord]:
        path = self._log_path if self._log_path is not None else codec.get_log_path()
        if not path.exists():
            return []
        records = []
        for line in path.read_bytes().splitlines(keepends=True):
            record = self._parse_record(line)
            if record is not None:
                records.append(record)
        records.sort(key=lambda record: record.lsn)
        return records
