"""Facade Failure Recovery Manager milik Group 2."""

from __future__ import annotations

from pathlib import Path

from common import config
from common.errors import LogError, MdbmsError
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
from failure_recovery.checkpoint.checkpoint_manager import CheckpointManager
from failure_recovery.checkpoint.checkpoint_store import CheckpointStore
from failure_recovery.log.log_reader import LogReader
from failure_recovery.log.wal_logger import WalLogger
from failure_recovery.results import RollbackResult
from failure_recovery.rollback.rollback_manager import RollbackManager


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
        self._master_path = master_path if master_path is not None else str(resolved.with_suffix(".checkpoint.json"))
        self._snapshot_dir = snapshot_dir if snapshot_dir is not None else str(resolved.parent / "snapshots")
        self._checkpoint_store = CheckpointStore(self._master_path, self._snapshot_dir)
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
        if self._sm is None:
            return TxResult(
                tx_id=tx_id,
                status=TxStatus.FAILED,
                ok=False,
                message="Dependensi SM belum dipasang",
            )
        self._rollback_interface = RollbackManager(
            self._log_writer,
            self._log_flusher,
            self._log_reader,
            self._sm,
        )
        try:
            result = self._rollback_interface.rollback(tx_id)
        except MdbmsError as exc:
            return TxResult(
                tx_id=tx_id,
                status=TxStatus.FAILED,
                ok=False,
                message=str(exc),
            )
        if result is RollbackResult.DONE:
            return TxResult(tx_id=tx_id, status=TxStatus.ABORTED, ok=True)
        if result is RollbackResult.ALREADY_ABORT:
            return TxResult(
                tx_id=tx_id,
                status=TxStatus.ABORTED,
                ok=True,
                message=f"T{tx_id} sudah abort",
            )
        if result is RollbackResult.COMMITTED:
            return TxResult(
                tx_id=tx_id,
                status=TxStatus.COMMITTED,
                ok=False,
                message=f"T{tx_id} sudah commit",
            )
        if result is RollbackResult.UNKNOWN_TX:
            return TxResult(
                tx_id=tx_id,
                status=TxStatus.FAILED,
                ok=False,
                message=f"T{tx_id} tidak dikenal",
            )
        return TxResult(
            tx_id=tx_id,
            status=TxStatus.FAILED,
            ok=False,
            message="Rollback tidak menghasilkan status",
        )

    def checkpoint(self) -> CheckpointResult:
        """Buat checkpoint lewat CheckpointManager."""
        if self._checkpoint_service is None:
            return CheckpointResult(ok=False, message="Dependensi SM dan CCM belum dipasang")
        return self._checkpoint_service.checkpoint()

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
        self._checkpoint_service = CheckpointManager(
            log_flusher=self._log_flusher,
            ccm=ccm,
            sm=sm,
            log_writer=self._log_writer,
            store=self._checkpoint_store,
            pitr=None,
            log_reader=self._log_reader,
        )

    def close(self) -> None:
        """Tutup sumber daya FRM."""
        if self._checkpoint_service is None:
            self._log_writer.close()
        else:
            self._checkpoint_service.stop_periodic()
            with self._checkpoint_service.checkpoint_lock:
                self._log_writer.close()
