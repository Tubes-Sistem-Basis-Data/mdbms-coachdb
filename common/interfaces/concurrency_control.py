"""Facade Concurrency Control Manager."""

from __future__ import annotations

from abc import ABC, abstractmethod

from common.transaction import (
    AccessResponse,
    Action,
    DataChange,
    ProtocolType,
    ResourceId,
    TxResult,
)


class ConcurrencyControlManager(ABC):
    """Siklus hidup transaksi dan izin akses. Dipanggil QP dan FRM, memanggil FRM."""

    @abstractmethod
    def begin_transaction(self) -> int:
        """Mulai transaksi baru, kembalikan tx_id."""

    @abstractmethod
    def validate_object(
        self,
        tx_id: int,
        resource: ResourceId,
        action: Action,
        change: DataChange | None = None,
    ) -> AccessResponse:
        """Minta izin akses. WRITE wajib menyertakan `change`; lsn dikembalikan jika ALLOWED."""

    @abstractmethod
    def commit(self, tx_id: int) -> TxResult:
        """Commit transaksi dan lepas lock."""

    @abstractmethod
    def abort(self, tx_id: int) -> TxResult:
        """Abort transaksi lewat FRM.rollback, lalu lepas lock."""

    @abstractmethod
    def set_protocol(self, protocol: ProtocolType) -> None:
        """Ganti protokol. Raise jika masih ada transaksi aktif."""

    @abstractmethod
    def get_protocol(self) -> ProtocolType:
        """Protokol yang sedang aktif."""

    @abstractmethod
    def initialize(self, last_tx_id: int) -> None:
        """Set penghitung tx_id setelah recovery. Dipanggil main.py."""

    @abstractmethod
    def pause_new_transactions(self) -> None:
        """Tahan transaksi baru (untuk checkpoint FRM)."""

    @abstractmethod
    def wait_until_idle(self) -> None:
        """Blok sampai tidak ada transaksi aktif."""

    @abstractmethod
    def resume_transactions(self) -> None:
        """Izinkan lagi transaksi baru."""

    """ Nanti request buat FRM 
    @abstractmethod
    def hold_new_transactions(self) -> None:

    @abstractmethod
    def wait_until_no_active(self, timeout: float) -> None:

    @abstractmethod
    def release_hold(self) -> None:
    """
