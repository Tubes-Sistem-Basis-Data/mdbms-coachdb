"""Facade Failure Recovery Manager milik Group 2."""

from __future__ import annotations

from datetime import datetime

from common.interfaces.failure_recovery import IFailureRecovery
from common.log import CheckpointResult, LogRecord, RestoreResult
from common.storage import RID
from common.transaction import ChangeOp, RollbackResult
from failure_recovery.recovery.analysis import RecoveryReport


class FailureRecoveryManager(IFailureRecovery):
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

    def attach_storage(self, sm) -> None:
        self._sm = sm

    def attach_ccm(self, ccm) -> None:
        self._ccm = ccm

    def log_begin(self, tx_id: int) -> int:
        return 0

    def log_write(
        self,
        tx_id: int,
        op: ChangeOp,
        rid: RID,
        before,
        after,
    ) -> int:
        return 0

    def log_commit(self, tx_id: int) -> int:
        return 0

    def rollback(self, tx_id: int) -> RollbackResult:
        return RollbackResult.UNKNOWN_TX

    def flush_log_until(self, lsn: int) -> None:
        return None

    def show_log(self, tx_id: int | None) -> list[LogRecord]:
        return []

    def checkpoint(self) -> CheckpointResult:
        return CheckpointResult(ok=False, message="belum diimplementasikan")

    def restore_to(
        self,
        target_time: datetime | None,
        target_tx_id: int | None,
    ) -> RestoreResult:
        return RestoreResult(ok=False, message="belum diimplementasikan")

    def recover(self) -> RecoveryReport | None:
        return None

    def close(self) -> None:
        return None
