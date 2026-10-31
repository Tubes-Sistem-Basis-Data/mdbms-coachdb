"""Facade Failure Recovery Manager."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import TYPE_CHECKING

from common.interfaces.concurrency_control import ConcurrencyControlManager
from common.interfaces.storage_manager import StorageManager
from common.log import CheckpointResult, LogRecord, RestoreResult
from common.storage import RID
from common.transaction import ChangeOp, RollbackResult

if TYPE_CHECKING:
    from failure_recovery.recovery.analysis import RecoveryReport


class IFailureRecovery(ABC):
    """Kontrak facade Failure Recovery Manager."""

    @abstractmethod
    def attach_storage(self, sm: StorageManager) -> None:
        """Suntik Storage Manager."""

    @abstractmethod
    def attach_ccm(self, ccm: ConcurrencyControlManager) -> None:
        """Suntik Concurrency Control Manager."""

    @abstractmethod
    def log_begin(self, tx_id: int) -> int:
        """Catat BEGIN dan kembalikan LSN."""

    @abstractmethod
    def log_write(
        self,
        tx_id: int,
        op: ChangeOp,
        table: str,
        rid: RID,
        before,
        after,
    ) -> int:
        """Catat perubahan data dan kembalikan LSN."""

    @abstractmethod
    def log_commit(self, tx_id: int) -> int:
        """Catat COMMIT dan kembalikan LSN."""

    @abstractmethod
    def rollback(self, tx_id: int) -> RollbackResult:
        """Batalkan transaksi beserta perubahannya."""

    @abstractmethod
    def flush_log_until(self, lsn: int) -> None:
        """Pastikan log sampai LSN tertentu permanen."""

    @abstractmethod
    def show_log(self, tx_id: int | None) -> list[LogRecord]:
        """Tampilkan isi log, bisa difilter per transaksi."""

    @abstractmethod
    def checkpoint(self) -> CheckpointResult:
        """Buat checkpoint."""

    @abstractmethod
    def restore_to(
        self,
        target_time: datetime | None,
        target_tx_id: int | None,
    ) -> RestoreResult:
        """Pulihkan ke waktu atau transaksi tertentu."""

    @abstractmethod
    def recover(self) -> RecoveryReport | None:
        """Crash recovery saat startup."""

    @abstractmethod
    def close(self) -> None:
        """Tutup sumber daya FRM."""


FailureRecoveryManager = IFailureRecovery
