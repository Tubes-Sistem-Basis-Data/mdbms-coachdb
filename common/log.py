"""Tipe data log dan recovery."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from common.transaction import DataChange


class LogKind(Enum):
    """Jenis catatan log."""

    BEGIN = "BEGIN"
    DATA = "DATA"
    COMMIT = "COMMIT"
    ABORT_REQUEST = "ABORT_REQUEST"
    ABORT = "ABORT"
    CHECKPOINT_BEGIN = "CHECKPOINT_BEGIN"
    CHECKPOINT_END = "CHECKPOINT_END"


@dataclass
class LogRecord:
    """Satu catatan log. `lsn` dan `timestamp` diisi FRM saat `write_log`."""

    kind: LogKind
    tx_id: int | None = None
    change: DataChange | None = None
    active_tx: list[int] = field(default_factory=list)
    lsn: int = -1
    timestamp: float = 0.0


@dataclass
class LogResult:
    """Hasil write_log."""

    ok: bool
    lsn: int | None = None
    message: str = ""


@dataclass
class CheckpointResult:
    """Hasil checkpoint."""

    ok: bool
    lsn: int | None = None
    message: str = ""


@dataclass
class RecoveryTarget:
    """Target point-in-time recovery. Tepat satu field harus terisi."""

    timestamp: float | None = None
    tx_id: int | None = None

    def __post_init__(self) -> None:
        if (self.timestamp is None) == (self.tx_id is None):
            raise ValueError("RecoveryTarget: isi tepat satu dari timestamp atau tx_id")


@dataclass
class RecoveryResult:
    """Hasil `recover` / `recover_to`. `last_tx_id` dipakai `CCM.initialize`."""

    ok: bool
    last_tx_id: int = 0
    last_lsn: int = 0
    message: str = ""
