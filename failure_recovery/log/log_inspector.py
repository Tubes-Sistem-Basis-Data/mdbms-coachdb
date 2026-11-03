"""Penampil isi log untuk driver dan debugging."""

from __future__ import annotations

from common.log import LogRecord
from failure_recovery.log import codec


class LogInspector:
    """Memformat catatan log agar mudah dibaca."""

    def __init__(self, reader=None) -> None:
        self._reader = reader

    def display(self, records) -> str:
        """Catatan tampil sebagai JSON per baris, format yang sama dengan isi wal.log."""
        baris = []
        for record in records:
            data = codec.serialize(record)
            if data is not None:
                baris.append(data.decode("utf-8").rstrip("\n"))
        return "\n".join(baris)

    def filter_by_tx(self, tx_id: int) -> list[LogRecord]:
        """Catatan satu transaksi, diambil langsung dari reader log."""
        return self._reader.read_log(tx_id)
