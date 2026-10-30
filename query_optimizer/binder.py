"""Purpose: bind tables/columns. Mock: no checks."""

from __future__ import annotations

from common.plan import ParsedQuery


def bind_query(query: ParsedQuery | None) -> ParsedQuery | None:
    """Purpose: validate names against catalog. Mock: just return query."""
    return query