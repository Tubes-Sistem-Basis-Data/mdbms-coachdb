"""Penyimpanan metadata checkpoint."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from common.errors import LogError
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
    try:
        lsn = payload["checkpoint_lsn"]
        snapshot_path = payload["snapshot_path"]
        active_tx = payload["active_tx"]
        max_tx_id = payload["max_tx_id"]
        completed = payload["completed"]
        if (
            type(lsn) is not int or lsn < 1
            or not isinstance(snapshot_path, str)
            or not isinstance(active_tx, list)
            or any(type(tx) is not int or tx < 1 for tx in active_tx)
            or type(max_tx_id) is not int or max_tx_id < 0
            or type(completed) is not bool
        ):
            return None
        return CheckpointMeta(
            checkpoint_lsn=lsn,
            snapshot_path=snapshot_path,
            timestamp=datetime.fromisoformat(payload["timestamp"]),
            active_tx=list(active_tx),
            max_tx_id=max_tx_id,
            completed=completed,
        )
    except (KeyError, TypeError, ValueError):
        return None


class CheckpointStore(CheckpointQueryInterface):
    """Menyimpan metadata checkpoint di master file dan direktori snapshot."""

    def __init__(self, master_path: str, snapshot_dir: str) -> None:
        self.master_path = str(master_path)
        self.snapshot_dir = str(snapshot_dir)
        self._lock = threading.RLock()

    def read_master(self) -> CheckpointMeta | None:
        """Baca metadata persisten; master yang belum ada berarti belum ada checkpoint."""
        with self._lock:
            try:
                with open(self.master_path, encoding="utf-8") as stream:
                    payload = json.load(stream)
            except FileNotFoundError:
                return None
            except (OSError, ValueError) as exc:
                raise LogError(f"Gagal membaca metadata checkpoint: {exc}") from exc
            metadata = _from_dict(payload)
            if metadata is None:
                raise LogError("Metadata checkpoint tidak valid")
            return metadata

    def write_master_atomic(self, metadata: CheckpointMeta) -> None:
        """Persistenkan metadata lalu ganti master secara atomik."""
        temporary = None
        with self._lock:
            try:
                payload = _to_dict(metadata)
                if _from_dict(payload) is None:
                    raise ValueError("metadata tidak valid")
                directory = Path(self.master_path).resolve().parent
                directory.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=directory,
                    prefix=".checkpoint-", suffix=".tmp", delete=False,
                ) as stream:
                    temporary = stream.name
                    json.dump(payload, stream, ensure_ascii=False)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.master_path)
            except (OSError, TypeError, ValueError, AttributeError) as exc:
                raise LogError(f"Gagal menyimpan metadata checkpoint: {exc}") from exc
            finally:
                if temporary is not None:
                    try:
                        os.unlink(temporary)
                    except OSError:
                        pass

    def find_latest_completed(self) -> CheckpointMeta | None:
        metadata = self.read_master()
        return metadata if metadata is not None and metadata.completed else None

    def find_snapshot_before(self, target: RecoveryTarget) -> CheckpointMeta | None:
        return None

    def _matches(self, metadata: CheckpointMeta | None, target: RecoveryTarget) -> bool:
        return False
