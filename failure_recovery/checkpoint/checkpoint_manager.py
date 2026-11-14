"""Pembuat checkpoint."""

from __future__ import annotations

import logging
import math
import threading
from datetime import datetime, timezone

from common.log import CheckpointResult, RecoveryTarget
from failure_recovery.checkpoint.checkpoint_store import CheckpointStore
from failure_recovery.checkpoint.meta import CheckpointMeta
from failure_recovery.results import RestoreResult
from failure_recovery.ports import (
    CheckpointInterface,
    LogFlusher,
    LogWriter,
)

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
        """Buat checkpoint saat seluruh transaksi aktif sudah selesai."""
        with self.checkpoint_lock:
            return self._checkpoint_locked()

    def _checkpoint_locked(self) -> CheckpointResult:
        result = CheckpointResult(ok=False)
        try:
            self._ccm.pause_new_transactions()
            self._ccm.wait_until_idle()

            latest = self._store.find_latest_completed()
            max_tx_id = latest.max_tx_id if latest is not None else 0
            if self._log_reader is not None:
                for record in self._log_reader.read_log(-1):
                    if record.tx_id is not None:
                        max_tx_id = max(max_tx_id, record.tx_id)

            begin_lsn = self._log_writer.log_checkpoint_begin()
            self._log_flusher.flush_log_until(begin_lsn)
            self._sm.flush()
            end_lsn = self._log_writer.log_checkpoint_end()
            self._log_flusher.flush_log_until(end_lsn)

            self._store.write_master_atomic(CheckpointMeta(
                checkpoint_lsn=end_lsn,
                snapshot_path="",
                timestamp=datetime.now(timezone.utc),
                max_tx_id=max_tx_id,
                completed=True,
            ))
            result = CheckpointResult(ok=True, lsn=end_lsn, message="Checkpoint selesai")
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

    def restore_to(self, target: RecoveryTarget) -> RestoreResult | None:
        if self._pitr is None:
            return RestoreResult(ok=False, message="PITR belum dipasang")
        with self.checkpoint_lock:
            try:
                if target.tx_id is not None:
                    restored = self._pitr.restore_to_tx(target.tx_id)
                else:
                    restored = self._pitr.restore_to_time(
                        datetime.fromtimestamp(target.timestamp, tz=timezone.utc)
                    )
            except Exception as exc:
                return RestoreResult(ok=False, message=f"Pemulihan gagal: {exc}")
            if restored is None:
                return RestoreResult(ok=False, message="PITR tidak menghasilkan status")
            if not restored.ok:
                return restored

            checkpoint = self._checkpoint_locked()
            if not checkpoint.ok:
                return RestoreResult(
                    ok=False,
                    message=(
                        f"{restored.message}; checkpoint setelah pemulihan gagal: "
                        f"{checkpoint.message}"
                    ),
                )
            return RestoreResult(
                ok=True,
                message=f"{restored.message}; {checkpoint.message}",
            )

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
