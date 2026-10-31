"""Penyimpanan metadata checkpoint."""

from __future__ import annotations

from common.log import RecoveryTarget
from failure_recovery.checkpoint.meta import CheckpointMeta
from failure_recovery.ports import CheckpointQueryInterface


def _to_dict(meta: CheckpointMeta) -> dict:
    return {
        "checkpoint_lsn": meta.checkpoint_lsn,
        "snapshot_path": meta.snapshot_path,
        "timestamp": meta.timestamp.isoformat(),
        "active_tx": list(meta.active_tx),
        "max_tx_id": meta.max_tx_id,
        "completed": meta.completed,
    }


def _from_dict(payload: dict) -> CheckpointMeta | None:
    return None


class CheckpointStore(CheckpointQueryInterface):
    """Menyimpan metadata checkpoint di master file dan direktori snapshot."""

    def __init__(self, master_path: str, snapshot_dir: str) -> None:
        self.master_path = str(master_path)
        self.snapshot_dir = str(snapshot_dir)

    def read_master(self) -> CheckpointMeta | None:
        return None

    def write_master_atomic(self, metadata: CheckpointMeta) -> None:
        return None

    def find_latest_completed(self) -> CheckpointMeta | None:
        return None

    def find_snapshot_before(self, target: RecoveryTarget) -> CheckpointMeta | None:
        return None

    def _matches(self, metadata: CheckpointMeta | None, target: RecoveryTarget) -> bool:
        return False
