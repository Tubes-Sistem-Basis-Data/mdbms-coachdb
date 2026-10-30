"""Purpose: parse a statement. Mock: treat everything as a SELECT."""

from __future__ import annotations

from common.plan import ParsedQuery, QueryKind


def parse_statement(sql: str) -> ParsedQuery:
    """Purpose: turn SQL into ParsedQuery. Mock: simple SELECT placeholder."""
    return ParsedQuery(sql=sql, kind=QueryKind.SELECT, plan=None, estimated_cost=0.0)
