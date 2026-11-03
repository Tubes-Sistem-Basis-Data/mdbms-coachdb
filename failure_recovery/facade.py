"""Facade Failure Recovery Manager milik Group 2."""

from __future__ import annotations

from pathlib import Path

from common import config
from common.errors import LogError
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
from failure_recovery.log.log_reader import LogReader
from failure_recovery.log.wal_logger import WalLogger


class FailureRecoveryManager(FailureRecoveryManagerBase):
    """Wiring dan delegasi ke subkomponen log, rollback, checkpoint, recovery."""

    def __init__(
        self,
        log_path: str | None = None,
        master_path: str | None = None,
        snapshot_dir: str | None = None,
    ) -> None:
        resolved = Path(log_path) if log_path else Path(config.DATA_DIR) / "wal.log"
        self._log_path = resolved
        self._master_path = master_path
        self._snapshot_dir = snapshot_dir
        self._log_writer = WalLogger(str(resolved))
        self._log_reader = LogReader(str(resolved))
        self._log_flusher = self._log_writer
        self._rollback_interface = None
        self._checkpoint_service = None
        self._recovery_service = None
        self._sm = None
        self._ccm = None

    def write_log(self, record: LogRecord) -> LogResult:
        """Tambah catatan log lewat WalLogger. Dipanggil CCM."""
        try:
            lsn = self._log_writer.append(record)
        except LogError as exc:
            return LogResult(ok=False, message=str(exc))
        return LogResult(ok=True, lsn=lsn)

    def flush_log(self, up_to_lsn: int) -> bool:
        """Pastikan log sampai `up_to_lsn` permanen. Dipanggil SM sebelum tulis page."""
        try:
            self._log_writer.flush_log_until(up_to_lsn)
        except LogError:
            return False
        return True

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
        return self._log_reader.read_log(-1 if tx_id is None else tx_id)

    def set_dependencies(self, sm: StorageManager, ccm: ConcurrencyControlManager) -> None:
        """Suntik SM dan CCM. Dipanggil main.py saat bootstrap."""
        self._sm = sm
        self._ccm = ccm

    def close(self) -> None:
        """Tutup sumber daya FRM."""
        self._log_writer.close()
