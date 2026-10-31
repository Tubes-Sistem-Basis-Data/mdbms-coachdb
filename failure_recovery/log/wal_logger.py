"""Penulis Write-Ahead Log."""

from __future__ import annotations

from common.log import LogRecord, RecoveryTarget
from common.storage import RID
from common.transaction import ChangeOp
from failure_recovery.log import codec
from failure_recovery.ports import LogFlusher, LogWriter


class WalLogger(LogWriter, LogFlusher):
    """Penulis WAL berbasis file JSON per baris."""

    def __init__(self, log_path: str) -> None:
        self.log_path = str(log_path)
        self.next_lsn = 1
        codec.set_log_path(self.log_path)
        self._load_state()

    def _load_state(self) -> None:
        return None

    def _prev_lsn(self, tx_id: int) -> int:
        return 0

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

    def log_abort_request(self, tx_id: int) -> int:
        return 0

    def log_abort_end(self, tx_id: int) -> int:
        return 0

    def log_clr(self, tx_id: int, rid: RID, before, after) -> int:
        return 0

    def log_checkpoint_begin(self) -> int:
        return 0

    def log_checkpoint_end(self) -> int:
        return 0

    def log_restore(self, target: RecoveryTarget) -> int:
        return 0

    def flush_log_until(self, lsn: int) -> None:
        return None

    def _append(self, record: LogRecord) -> int:
        return 0

    def _persist(self, lsn: int) -> None:
        return None
