"""Pembuat checkpoint."""

from __future__ import annotations

import threading

from common.log import CheckpointResult, RecoveryTarget, RestoreResult
from failure_recovery.ports import (
    CheckpointInterface,
    CheckpointQueryInterface,
    LogFlusher,
    LogWriter,
)


class CheckpointManager(CheckpointInterface):
    """Checkpoint periodik/manual dan delegasi PITR."""

    def __init__(
        self,
        log_flusher: LogFlusher,
        ccm,
        sm,
        log_writer: LogWriter,
        store: CheckpointQueryInterface,
        pitr,
        log_reader=None,
        interval_seconds: int = 60,
        timeout_seconds: float = 5.0,
    ) -> None:
        self.interval_seconds = interval_seconds
        self.timeout_seconds = timeout_seconds
        self.scheduler_thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.checkpoint_lock = threading.Lock()
        self._log_flusher = log_flusher
        self._ccm = ccm
        self._sm = sm
        self._log_writer = log_writer
        self._store = store
        self._pitr = pitr
        self._log_reader = log_reader

    def checkpoint(self) -> CheckpointResult | None:
        return None

    def restore_to(self, target: RecoveryTarget) -> RestoreResult | None:
        return None

    def start_periodic(self) -> None:
        return None

    def stop_periodic(self) -> None:
        return None
