"""Error internal Storage Manager, turunan error di common."""

from __future__ import annotations

from common.errors import SemanticError, StorageError


class TableNotFoundError(SemanticError):
    """Tabel tidak ada di katalog."""


class IndexDefinitionError(SemanticError):
    """Definisi index tidak valid: kolom tidak ada, nama ganda, atau operasi tak didukung."""


class SchemaViolationError(StorageError):
    """Nilai tidak sesuai skema: tipe salah, terlalu panjang, atau NULL tak diizinkan."""
