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


class LogError(MdbmsError):
    """Kegagalan operasi log."""


class TableNotFoundError(SemanticError):
<<<<<<< Updated upstream
    """Tabel tidak ada di katalog."""


class IndexDefinitionError(SemanticError):
    """Definisi atau operasi index tidak valid."""


class SchemaViolationError(StorageError):
    """Nilai atau definisi tidak sesuai skema."""
=======
    """Tabel tidak ada di katalog. Dilempar SM, misalnya dari get_schema."""


class IndexDefinitionError(SemanticError):
    """Definisi index tidak valid: kolom tidak ada, nama ganda, atau operasi tak didukung."""


class SchemaViolationError(StorageError):
    """Nilai tidak sesuai skema: tipe salah, terlalu panjang, atau NULL tak diizinkan."""


class LogWriteError(LogError):
    """Catatan log gagal ditulis atau di-flush ke disk."""


class RecoveryError(MdbmsError):
    """Recovery gagal. Dipakai main.py untuk pesan startup yang jelas."""
>>>>>>> Stashed changes
