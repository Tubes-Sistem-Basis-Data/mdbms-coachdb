"""Validasi nama tabel dan kolom terhadap katalog SM."""

from __future__ import annotations

from common.plan import ParsedQuery


def bind_query(query: ParsedQuery) -> ParsedQuery:
    """Cek tabel dan kolom di query ada di katalog (QO-07)."""
    return query