"""Tipe data log dan recovery."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from common.storage import RID
from common.transaction import RECORD_ABSENT, _NoRecord


class LogKind(Enum):
    """Jenis catatan log."""

    BEGIN = "BEGIN"
    COMMIT = "COMMIT"
    ABORT_REQUEST = "ABORT_REQUEST"
    ABORT_END = "ABORT_END"
    INSERT = "INSERT"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    CLR = "CLR"
    CHECKPOINT_BEGIN = "CHECKPOINT_BEGIN"
    CHECKPOINT_END = "CHECKPOINT_END"
    RESTORE = "RESTORE"


# @dataclass(kw_only=True) nanti ubah jadi ini
@dataclass
class LogRecord:
    """Satu catatan log. `lsn` dan `timestamp` diisi FRM saat append."""

    lsn: int | None = -1
    prev_lsn: int | None = -1
    tx_id: int | None = -1
    kind: LogKind | None = LogKind.BEGIN
    table: str | None = ""
    rid: RID | None = field(default_factory=lambda: RID("", 0, 0))
    before: tuple | _NoRecord | None = RECORD_ABSENT
    after: tuple | _NoRecord | None = RECORD_ABSENT
    timestamp: datetime | None = field(default_factory=datetime.now)


@dataclass
class RecoveryTarget:
    """Target point-in-time recovery."""

    timestamp: datetime | None = None
    tx_id: int | None = None


@dataclass
class CheckpointResult:
    """Hasil checkpoint."""

    ok: bool
    lsn: int | None = None
    message: str = ""


@dataclass
class RestoreResult:
    """Hasil pemulihan ke waktu atau transaksi tertentu."""

    ok: bool
    message: str = ""
