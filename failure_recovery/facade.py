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
from failure_recovery.checkpoint.pitr_manager import PITRManager
from failure_recovery.log.log_reader import LogReader
from failure_recovery.log.wal_logger import WalLogger
from failure_recovery.recovery.analysis import RecoveryStatus
from failure_recovery.recovery.analyzer import RecoveryAnalyzer
from failure_recovery.recovery.recovery_manager import RecoveryManager
from failure_recovery.recovery.redo_executor import RedoExecutor
from failure_recovery.recovery.undo_coordinator import UndoCoordinator
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
        if self._recovery_service is None:
            return RecoveryResult(ok=False, message="Dependensi SM dan CCM belum dipasang")
        try:
            report = self._recovery_service.recover()
        except MdbmsError as exc:
            return RecoveryResult(ok=False, message=str(exc))
        except Exception as exc:
            return RecoveryResult(ok=False, message=f"Recovery gagal: {exc}")
        if report is None:
            return RecoveryResult(ok=False, message="Recovery tidak menghasilkan report")

        last_lsn = max((record.lsn for record in self.read_log()), default=0)
        if report.status is not RecoveryStatus.SUCCESS:
            message = "; ".join(report.errors) or f"Recovery {report.status.value.lower()}"
            return RecoveryResult(
                ok=False,
                last_tx_id=report.max_tx_id,
                last_lsn=last_lsn,
                message=message,
            )
        message = (
            f"Recovery selesai: {report.redo_count} redo, "
            f"{report.undo_count} undo, {len(report.undone_tx)} transaksi dibatalkan"
        )
        return RecoveryResult(
            ok=True,
            last_tx_id=report.max_tx_id,
            last_lsn=last_lsn,
            message=message,
        )

    def recover_to(self, target: RecoveryTarget) -> RecoveryResult:
        """Pemulihan ke waktu atau transaksi tertentu lewat PITRManager."""
        if self._checkpoint_service is None:
            return RecoveryResult(ok=False, message="Dependensi SM dan CCM belum dipasang")
        try:
            result = self._checkpoint_service.restore_to(target)
        except MdbmsError as exc:
            return RecoveryResult(ok=False, message=str(exc))
        except Exception as exc:
            return RecoveryResult(ok=False, message=f"Pemulihan gagal: {exc}")
        if result is None:
            return RecoveryResult(ok=False, message="Pemulihan tidak menghasilkan status")

        records = self.read_log()
        last_tx_id = max(
            (record.tx_id for record in records if record.tx_id is not None),
            default=0,
        )
        last_lsn = max((record.lsn for record in records), default=0)
        return RecoveryResult(
            ok=result.ok,
            last_tx_id=last_tx_id,
            last_lsn=last_lsn,
            message=result.message or (
                "Pemulihan selesai" if result.ok else "Pemulihan gagal"
            ),
        )

    def read_log(self, tx_id: int | None = None) -> list[LogRecord]:
        """Isi log lewat LogReader, bisa difilter per transaksi."""
        return self._log_reader.read_log(-1 if tx_id is None else tx_id)

    def set_dependencies(self, sm: StorageManager, ccm: ConcurrencyControlManager) -> None:
        """Suntik SM dan CCM. Dipanggil main.py saat bootstrap."""
        self._sm = sm
        self._ccm = ccm
        self._rollback_interface = RollbackManager(
            self._log_writer,
            self._log_flusher,
            self._log_reader,
            sm,
        )
        pitr = PITRManager(
            store=self._checkpoint_store,
            sm=sm,
            log_writer=self._log_writer,
            log_reader=self._log_reader,
            log_flusher=self._log_flusher,
        )
        self._checkpoint_service = CheckpointManager(
            log_flusher=self._log_flusher,
            ccm=ccm,
            sm=sm,
            log_writer=self._log_writer,
            store=self._checkpoint_store,
            pitr=pitr,
            log_reader=self._log_reader,
        )
        self._recovery_service = RecoveryManager(
            analyzer=RecoveryAnalyzer(self._log_reader),
            redo=RedoExecutor(sm),
            undo=UndoCoordinator(self._rollback_interface),
            checkpoint_query=self._checkpoint_store,
            checkpoint_service=self._checkpoint_service,
            log_flusher=self._log_flusher,
            sm=sm,
            ccm=ccm,
        )

    def close(self) -> None:
        """Tutup sumber daya FRM."""
        if self._checkpoint_service is None:
            self._log_writer.close()
        else:
            self._checkpoint_service.stop_periodic()
            with self._checkpoint_service.checkpoint_lock:
                self._log_writer.close()
