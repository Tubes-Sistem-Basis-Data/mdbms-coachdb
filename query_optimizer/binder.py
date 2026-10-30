"""Purpose: bind tables/columns. Mock: no checks."""

from __future__ import annotations

from common.plan import ParsedQuery


def bind_query(query: ParsedQuery) -> ParsedQuery:
    """Validate names against catalog"""
    return query