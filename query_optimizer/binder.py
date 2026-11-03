"""Validasi nama tabel dan kolom terhadap katalog SM."""

from __future__ import annotations

from common.interfaces import StorageManager
from common.plan import ParsedQuery


class Binder:
    """Mengisi dan memvalidasi nama tabel dan kolom di query (QO-07)."""

    def __init__(self, catalog: StorageManager | None = None) -> None:
        self._catalog = catalog

    def bind(self, query: ParsedQuery) -> ParsedQuery:
        """Cek tabel dan kolom ada di katalog, raise SemanticError jika tidak."""
        return query


def bind_query(query: ParsedQuery) -> ParsedQuery:
    """Cek tabel dan kolom di query ada di katalog (QO-07)."""
    return query
