"""Tipe data internal hasil analisis recovery."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from common.log import LogRecord


class RecoveryStatus(Enum):
    """Status akhir proses recovery."""

    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


@dataclass
class AnalysisResult:
    """Hasil fase analisis log."""

    start_lsn: int
    last_lsn: int
    max_tx_id: int
    tx_state: dict
    changes: list[LogRecord]
    losers: list[int]


@dataclass
class RecoveryReport:
    """Ringkasan hasil recovery."""

    status: RecoveryStatus
    start_lsn: int
    committed_tx: list[int] = field(default_factory=list)
    aborted_tx: list[int] = field(default_factory=list)
    undone_tx: list[int] = field(default_factory=list)
    redo_count: int = 0
    undo_count: int = 0
    max_tx_id: int = 0
    errors: list[str] = field(default_factory=list)
