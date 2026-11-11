"""Error Storage Manager. Kelasnya sudah pindah ke common.errors, modul ini hanya re-export."""

from __future__ import annotations

from common.errors import IndexDefinitionError, SchemaViolationError, StorageError, TableNotFoundError

__all__ = ["IndexDefinitionError", "SchemaViolationError", "StorageError", "TableNotFoundError"]
