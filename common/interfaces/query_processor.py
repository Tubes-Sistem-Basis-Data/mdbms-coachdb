"""Facade Query Processor."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from common.result import ExecutionResult


@dataclass
class Session:
    """Sesi satu client. `tx_id` terisi selama ada transaksi aktif."""

    session_id: int
    tx_id: int | None = None
    in_explicit_tx: bool = False


class QueryProcessor(ABC):
    """Orkestrator eksekusi query. Dipanggil server/CLI, memanggil QO, CCM, dan SM."""

    @abstractmethod
    def open_session(self) -> Session:
        """Buka sesi baru untuk satu client."""

    @abstractmethod
    def close_session(self, session_id: int) -> None:
        """Tutup sesi. Transaksi yang masih aktif di-abort."""

    @abstractmethod
    def execute_query(self, sql: str, session: Session) -> ExecutionResult:
        """Jalankan satu perintah SQL dalam konteks sesi. Tidak melempar MdbmsError."""
