"""Metadata checkpoint internal."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class CheckpointMeta:
    """Metadata satu checkpoint, termasuk path snapshot."""

    checkpoint_lsn: int
    snapshot_path: str
    timestamp: datetime
    active_tx: list[int] = field(default_factory=list)
    max_tx_id: int = 0
    completed: bool = False
