"""Interface internal antar-subkomponen Failure Recovery Manager."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from common.log import CheckpointResult, LogRecord, RecoveryTarget, RestoreResult
from common.storage import RID
from common.transaction import ChangeOp, RollbackResult

if TYPE_CHECKING:
    from failure_recovery.checkpoint.meta import CheckpointMeta
    from failure_recovery.recovery.analysis import RecoveryReport


class LogWriter(ABC):
    """Port penulis catatan log."""

    @abstractmethod
    def log_begin(self, tx_id: int) -> int:
        """Catat BEGIN dan kembalikan LSN."""

    @abstractmethod
    def log_write(
        self,
        tx_id: int,
        op: ChangeOp,
        rid: RID,
        before,
        after,
    ) -> int:
        """Catat perubahan data dan kembalikan LSN."""

    @abstractmethod
    def log_commit(self, tx_id: int) -> int:
        """Catat COMMIT dan kembalikan LSN."""

    @abstractmethod
    def log_abort_request(self, tx_id: int) -> int:
        """Catat ABORT_REQUEST dan kembalikan LSN."""

    @abstractmethod
    def log_abort_end(self, tx_id: int) -> int:
        """Catat ABORT_END dan kembalikan LSN."""

    @abstractmethod
    def log_clr(self, tx_id: int, rid: RID, before, after) -> int:
        """Catat CLR dan kembalikan LSN."""

    @abstractmethod
    def log_checkpoint_begin(self) -> int:
        """Catat CHECKPOINT_BEGIN dan kembalikan LSN."""

    @abstractmethod
    def log_checkpoint_end(self) -> int:
        """Catat CHECKPOINT_END dan kembalikan LSN."""

    @abstractmethod
    def log_restore(self, target: RecoveryTarget) -> int:
        """Catat RESTORE dan kembalikan LSN."""


class LogFlusher(ABC):
    """Port flush log."""

    @abstractmethod
    def flush_log_until(self, lsn: int) -> None:
        """Pastikan log sampai LSN tertentu permanen."""


class LogReadInterface(ABC):
    """Port pembaca log."""

    @abstractmethod
    def read_log(self, filter_tx: int) -> list[LogRecord]:
        """Baca seluruh log, filter per transaksi jika `filter_tx` >= 0."""

    @abstractmethod
    def forward_scan(self, from_lsn: int) -> list[LogRecord]:
        """Baca log dari LSN tertentu ke depan."""

    @abstractmethod
    def backward_scan(self, tx_id: int) -> list[LogRecord]:
        """Baca log satu transaksi dengan urutan LSN terbalik."""


class RollbackInterface(ABC):
    """Port rollback transaksi."""

    @abstractmethod
    def rollback(self, tx_id: int) -> RollbackResult | None:
        """Batalkan transaksi beserta perubahannya."""


class CheckpointInterface(ABC):
    """Port checkpoint dan restore."""

    @abstractmethod
    def checkpoint(self) -> CheckpointResult | None:
        """Buat checkpoint."""

    @abstractmethod
    def restore_to(self, target: RecoveryTarget) -> RestoreResult | None:
        """Pulihkan ke target tertentu."""


class CheckpointQueryInterface(ABC):
    """Port query metadata checkpoint."""

    @abstractmethod
    def find_latest_completed(self) -> CheckpointMeta | None:
        """Checkpoint selesai terbaru, None jika tidak ada."""


class RecoveryInterface(ABC):
    """Port crash recovery."""

    @abstractmethod
    def recover(self) -> RecoveryReport | None:
        """Jalankan crash recovery."""

    @abstractmethod
    def is_ready(self) -> bool:
        """True jika recovery sudah selesai."""
