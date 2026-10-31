"""Tipe data untuk komunikasi QP, CCM, dan FRM."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Self, cast

from common.storage import RID


class Action(Enum):
    """Jenis akses yang diminta ke CCM."""

    READ = "READ"
    WRITE = "WRITE"


@dataclass(frozen=True)
class ResourceId:
    """Objek yang dikunci. `rid=None` berarti seluruh tabel."""

    table: str
    rid: RID | None = None


class ChangeOp(Enum):
    """Jenis perubahan data."""

    INSERT = "INSERT"
    UPDATE = "UPDATE"
    DELETE = "DELETE"


class RollbackResult(Enum):
    """Hasil pembatalan transaksi."""

    DONE = "DONE"
    ALREADY_ABORT = "ALREADY_ABORT"
    COMMITTED = "COMMITTED"
    UNKNOWN_TX = "UNKNOWN_TX"


class _NoRecord:
    """Penanda tidak ada baris (before INSERT, after DELETE). Bukan None."""

    _instance: _NoRecord | None = None

    def __new__(cls) -> Self:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cast(Self, cls._instance)

    def __repr__(self) -> str:
        return "NO_RECORD"

    def __reduce__(self) -> str:
        return "NO_RECORD"

    def __copy__(self) -> _NoRecord:
        return self

    def __deepcopy__(self, memo: dict) -> _NoRecord:
        return self

    def __bool__(self) -> bool:
        return False


NO_RECORD = _NoRecord()
RECORD_ABSENT = NO_RECORD


@dataclass
class DataChange:
    """Before/after image satu baris untuk WAL."""

    table: str
    rid: RID
    op: ChangeOp
    before: list | _NoRecord
    after: list | _NoRecord


class AccessStatus(Enum):
    """Jawaban CCM atas permintaan akses."""

    ALLOWED = "ALLOWED"
    WAIT = "WAIT"
    ABORTED = "ABORTED"


@dataclass
class AccessResponse:
    """Hasil `validate_object`. `lsn` terisi untuk WRITE yang ALLOWED."""

    status: AccessStatus
    lsn: int | None = None
    message: str = ""


class ProtocolType(Enum):
    """Protokol concurrency control."""

    LOCK_BASED = "LOCK_BASED"
    TIMESTAMP_BASED = "TIMESTAMP_BASED"


class TxStatus(Enum):
    """Status transaksi."""

    ACTIVE = "ACTIVE"
    PARTIALLY_COMMITTED = "PARTIALLY_COMMITTED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"
    COMMITTED = "COMMITTED"


@dataclass
class TxResult:
    """Hasil commit, abort, atau rollback."""

    tx_id: int
    status: TxStatus
    ok: bool
    message: str = ""
