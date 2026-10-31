"""Penampil isi log untuk driver dan debugging."""

from __future__ import annotations

from common.log import LogRecord


class LogInspector:
    """Memformat catatan log agar mudah dibaca."""

    def display(self, records) -> str:
        return ""

    def filter_by_tx(self, tx_id: int) -> list[LogRecord]:
        return []
