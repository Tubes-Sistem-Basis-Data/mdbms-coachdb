"""Tipe hasil internal Failure Recovery Manager. Tidak dipakai komponen lain."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RollbackResult(Enum):
    """Hasil pembatalan transaksi di RollbackManager."""

    DONE = "DONE"
    ALREADY_ABORT = "ALREADY_ABORT"
    COMMITTED = "COMMITTED"
    UNKNOWN_TX = "UNKNOWN_TX"


@dataclass
class RestoreResult:
    """Hasil pemulihan ke waktu atau transaksi tertentu di PITRManager."""

    ok: bool
    message: str = ""
