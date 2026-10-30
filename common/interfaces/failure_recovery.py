"""Facade Failure Recovery Manager."""

from __future__ import annotations

from abc import ABC, abstractmethod

from common.interfaces.concurrency_control import ConcurrencyControlManager
from common.interfaces.storage_manager import StorageManager
from common.log import (
    CheckpointResult,
    LogRecord,
    LogResult,
    RecoveryResult,
    RecoveryTarget,
)
from common.transaction import TxResult


class FailureRecoveryManager(ABC):
    """WAL, rollback, checkpoint, dan recovery. Dipanggil CCM dan SM, memanggil SM."""

    @abstractmethod
    def write_log(self, record: LogRecord) -> LogResult:
        """Tambah catatan log. FRM mengisi `record.lsn` dan `record.timestamp`."""

    @abstractmethod
    def flush_log(self, up_to_lsn: int) -> bool:
        """Pastikan log sampai `up_to_lsn` sudah permanen. Dipanggil SM sebelum tulis page."""

    @abstractmethod
    def rollback(self, tx_id: int) -> TxResult:
        """Undo semua perubahan transaksi lewat SM. Dipanggil CCM.abort."""

    @abstractmethod
    def checkpoint(self) -> CheckpointResult:
        """Buat checkpoint (memakai pause/wait/resume CCM dan SM.flush)."""

    @abstractmethod
    def recover(self) -> RecoveryResult:
        """Crash recovery saat startup. Dipanggil main.py."""

    @abstractmethod
    def recover_to(self, target: RecoveryTarget) -> RecoveryResult:
        """Pemulihan ke waktu atau transaksi tertentu."""

    @abstractmethod
    def read_log(self, tx_id: int | None = None) -> list[LogRecord]:
        """Isi log, bisa difilter per transaksi."""

    @abstractmethod
    def set_dependencies(self, sm: StorageManager, ccm: ConcurrencyControlManager) -> None:
        """Suntik SM dan CCM. Dipanggil main.py saat bootstrap."""
