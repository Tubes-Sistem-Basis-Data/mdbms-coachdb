"""Pembuat checkpoint."""

from __future__ import annotations

import logging
import math
import threading
from datetime import datetime, timezone
from pathlib import Path

from common.errors import RecoveryError
from common.log import CheckpointResult, LogKind, RecoveryTarget
from failure_recovery.checkpoint.checkpoint_store import CheckpointStore
from failure_recovery.checkpoint.meta import CheckpointMeta
from failure_recovery.ports import (
    CheckpointInterface,
    LogFlusher,
    LogWriter,
)
from failure_recovery.results import RestoreResult

logger = logging.getLogger("coachdb.frm")


class CheckpointManager(CheckpointInterface):
    """Checkpoint periodik/manual dan delegasi PITR."""

    def __init__(
        self,
        log_flusher: LogFlusher,
        ccm,
        sm,
        log_writer: LogWriter,
        store: CheckpointStore,
        pitr,
        log_reader=None,
        interval_seconds: int = 60,
        timeout_seconds: float = 5.0,
    ) -> None:
        if not math.isfinite(interval_seconds) or interval_seconds <= 0:
            raise ValueError("Interval checkpoint harus positif dan terbatas")
        self.interval_seconds = interval_seconds
        self.timeout_seconds = timeout_seconds
        self.scheduler_thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.checkpoint_lock = threading.Lock()
        self._scheduler_lock = threading.Lock()
        self._log_flusher = log_flusher
        self._ccm = ccm
        self._sm = sm
        self._log_writer = log_writer
        self._store = store
        self._pitr = pitr
        self._log_reader = log_reader

    def checkpoint(self) -> CheckpointResult:
        """Buat checkpoint dan rekam transaksi yang masih aktif."""
        with self.checkpoint_lock:
            return self._checkpoint_locked()

    def _checkpoint_locked(self) -> CheckpointResult:
        result = CheckpointResult(ok=False)
        try:
            self._ccm.pause_new_transactions()
            self._ccm.wait_until_idle()

            result = self._write_checkpoint()
        except Exception as exc:
            result = CheckpointResult(ok=False, message=f"Checkpoint gagal: {exc}")
        finally:
            try:
                self._ccm.resume_transactions()
            except Exception as exc:
                result = CheckpointResult(
                    ok=False, lsn=result.lsn,
                    message=f"Gagal melanjutkan transaksi setelah checkpoint: {exc}",
                )
        return result

    def _write_checkpoint(self, restore_boundary_lsn: int | None = None) -> CheckpointResult:
        latest = self._store.find_latest_completed()
        if restore_boundary_lsn is None and latest is not None:
            restore_boundary_lsn = latest.restore_boundary_lsn
        active_tx, log_max_tx_id = self._wal_state()
        max_tx_id = max(latest.max_tx_id if latest is not None else 0, log_max_tx_id)

        begin_lsn = self._log_writer.log_checkpoint_begin()
        self._log_flusher.flush_log_until(begin_lsn)
        self._sm.flush()
        end_lsn = self._log_writer.log_checkpoint_end()
        self._log_flusher.flush_log_until(end_lsn)

        try:
            snapshot_path = self._snapshot(end_lsn)
            metadata = CheckpointMeta(
                checkpoint_lsn=end_lsn,
                snapshot_path=snapshot_path,
                timestamp=datetime.now(timezone.utc),
                active_tx=active_tx,
                max_tx_id=max_tx_id,
                completed=True,
                restore_boundary_lsn=restore_boundary_lsn,
            )
            if snapshot_path:
                self._store.write_snapshot_metadata(metadata)
            self._store.write_master_atomic(metadata)
        except Exception:
            self._store.discard_snapshot(end_lsn)
            raise
        return CheckpointResult(ok=True, lsn=end_lsn, message="Checkpoint selesai")

    def _wal_state(self) -> tuple[list[int], int]:
        """Transaksi aktif dan tx_id maksimum menurut WAL."""
        if self._log_reader is None:
            return [], 0
        active: set[int] = set()
        max_tx_id = 0
        for record in self._log_reader.read_log(-1):
            if record.tx_id is None:
                continue
            max_tx_id = max(max_tx_id, record.tx_id)
            if record.kind is LogKind.BEGIN:
                active.add(record.tx_id)
            elif record.kind in (LogKind.COMMIT, LogKind.ABORT):
                active.discard(record.tx_id)
        return sorted(active), max_tx_id

    def _snapshot(self, checkpoint_lsn: int) -> str:
        """Ambil snapshot jika SM menyediakan operasi tersebut."""
        snapshot_to = getattr(self._sm, "snapshot_to", None)
        if not callable(snapshot_to):
            return ""
        path = self._store.snapshot_path(checkpoint_lsn)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        snapshot_to(path)
        return path

    def _rebuild_indexes(self) -> None:
        """Bangun ulang index setelah PITR jika SM mendukungnya."""
        rebuild = getattr(self._sm, "rebuild_indexes", None)
        if callable(rebuild):
            rebuild()

    def restore_to(self, target: RecoveryTarget) -> RestoreResult | None:
        if self._pitr is None:
            return RestoreResult(ok=False, message="PITR belum dipasang")
        with self.checkpoint_lock:
            try:
                self._ccm.pause_new_transactions()
                self._ccm.wait_until_idle()
                result = self._pitr.restore(target, self._finalize_restore)
            except Exception as exc:
                result = RestoreResult(ok=False, message=f"Pemulihan gagal: {exc}")
            finally:
                try:
                    self._ccm.resume_transactions()
                except Exception as exc:
                    logger.error("Gagal melanjutkan transaksi setelah pemulihan: %s", exc)
            return result

    def _finalize_restore(self, target: RecoveryTarget, boundary_lsn: int) -> str:
        try:
            self._rebuild_indexes()
        except Exception as exc:
            raise RecoveryError(f"rebuild index gagal: {exc}") from exc

        with self._log_writer.restore_batch():
            restore_lsn = self._log_writer.log_restore(target)
            self._log_flusher.flush_log_until(restore_lsn)
            try:
                return self._write_checkpoint(boundary_lsn).message
            except Exception as exc:
                raise RecoveryError(f"checkpoint setelah pemulihan gagal: {exc}") from exc

    def start_periodic(self) -> None:
        """Mulai satu thread daemon untuk checkpoint berkala."""
        with self._scheduler_lock:
            if self.scheduler_thread is not None and self.scheduler_thread.is_alive():
                return
            self.stop_event.clear()
            self.scheduler_thread = threading.Thread(
                target=self._run_periodic, name="coachdb-checkpoint", daemon=True,
            )
            self.scheduler_thread.start()

    def stop_periodic(self) -> None:
        """Hentikan penjadwalan dan tunggu checkpoint yang sedang berjalan."""
        with self._scheduler_lock:
            self.stop_event.set()
            worker = self.scheduler_thread
        if worker is not None and worker is not threading.current_thread():
            worker.join()
            with self._scheduler_lock:
                if self.scheduler_thread is worker:
                    self.scheduler_thread = None

    def _run_periodic(self) -> None:
        while not self.stop_event.wait(self.interval_seconds):
            result = self.checkpoint()
            if not result.ok:
                logger.warning("%s", result.message)
