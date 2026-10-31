"""Hierarki error mDBMS. QP menangkap MdbmsError menjadi ExecutionResult gagal."""

from __future__ import annotations


class MdbmsError(Exception):
    """Basis semua error mDBMS."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class QuerySyntaxError(MdbmsError):
    """Kesalahan sintaks SQL, dilempar QO saat parsing."""

    def __init__(self, message: str, position: int = 0, token: str = "") -> None:
        super().__init__(message)
        self.position = position
        self.token = token


class SemanticError(MdbmsError):
    """Objek (tabel/kolom/index) tidak valid terhadap katalog."""

    def __init__(self, message: str, object_name: str = "") -> None:
        super().__init__(message)
        self.object_name = object_name


class TransactionAbortedError(MdbmsError):
    """Transaksi dibatalkan oleh CCM."""

    def __init__(self, message: str, tx_id: int) -> None:
        super().__init__(message)
        self.tx_id = tx_id


class StorageError(MdbmsError):
    """Kegagalan operasi storage."""


class SchemaViolationError(StorageError):
    """Nilai tidak sesuai skema: tipe salah, terlalu panjang, atau NULL tak diizinkan."""


class LogError(MdbmsError):
    """Kegagalan operasi log."""
