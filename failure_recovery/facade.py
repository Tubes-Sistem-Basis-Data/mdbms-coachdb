"""Facade Failure Recovery Manager milik Group 2."""

from __future__ import annotations

from common.interfaces import ConcurrencyControlManager, StorageManager
from common.interfaces import FailureRecoveryManager as FailureRecoveryManagerBase
from common.log import (
    CheckpointResult,
    LogRecord,
    LogResult,
    RecoveryResult,
    RecoveryTarget,
)
from common.transaction import TxResult, TxStatus


class FailureRecoveryManager(FailureRecoveryManagerBase):
    """Wiring dan delegasi ke subkomponen log, rollback, checkpoint, recovery."""

    def __init__(
        self,
        log_path: str | None = None,
        master_path: str | None = None,
        snapshot_dir: str | None = None,
    ) -> None:
        self._log_writer = None
        self._log_flusher = None
        self._log_reader = None
        self._rollback_interface = None
        self._checkpoint_service = None
        self._recovery_service = None
        self._sm = None
        self._ccm = None

    def write_log(self, record: LogRecord) -> LogResult:
        """Tambah catatan log lewat WalLogger. Dipanggil CCM."""
        return LogResult(ok=False, message="belum diimplementasikan")

    def flush_log(self, up_to_lsn: int) -> bool:
        """Pastikan log sampai `up_to_lsn` permanen. Dipanggil SM sebelum tulis page."""
        return False

    def rollback(self, tx_id: int) -> TxResult:
        """Undo perubahan transaksi lewat RollbackManager. Dipanggil CCM.abort."""
        return TxResult(tx_id=tx_id, status=TxStatus.FAILED, ok=False, message="belum diimplementasikan")

    def checkpoint(self) -> CheckpointResult:
        """Buat checkpoint lewat CheckpointManager."""
        return CheckpointResult(ok=False, message="belum diimplementasikan")

    def recover(self) -> RecoveryResult:
        """Crash recovery lewat RecoveryManager. Dipanggil main.py saat startup."""
        return RecoveryResult(ok=True, message="belum diimplementasikan")

    def recover_to(self, target: RecoveryTarget) -> RecoveryResult:
        """Pemulihan ke waktu atau transaksi tertentu lewat PITRManager."""
        return RecoveryResult(ok=False, message="belum diimplementasikan")

    def read_log(self, tx_id: int | None = None) -> list[LogRecord]:
        """Isi log lewat LogReader, bisa difilter per transaksi."""
        return []

    def set_dependencies(self, sm: StorageManager, ccm: ConcurrencyControlManager) -> None:
        """Suntik SM dan CCM. Dipanggil main.py saat bootstrap."""
        self._sm = sm
        self._ccm = ccm

    def close(self) -> None:
        """Tutup sumber daya FRM."""
        return None
