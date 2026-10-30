"""Dumbest possible QO mock.

Purpose: parse SQL and return a safe placeholder plan. Real optimizer does the
real work later.
"""

from __future__ import annotations

from common.errors import QuerySyntaxError
from common.interfaces import QueryOptimizer
from common.plan import ParsedQuery, PlanNode, Project, QueryKind, TableScan, TransactionCommand


class MockQueryOptimizer(QueryOptimizer):
    """Return 0.0 and a dummy plan."""

    def parse_query(self, sql: str) -> ParsedQuery:
        """Purpose: parse SQL. Mock: accept only BEGIN/COMMIT/ABORT/SELECT."""
        s = sql.strip().rstrip(";")
        if not s:
            raise QuerySyntaxError("mock: empty query", position=0, token="")
        u = s.upper()
        if u.startswith("BEGIN"):
            return ParsedQuery(sql, QueryKind.TRANSACTION, command=TransactionCommand.BEGIN)
        if u.startswith("COMMIT"):
            return ParsedQuery(sql, QueryKind.TRANSACTION, command=TransactionCommand.COMMIT)
        if u.startswith("ABORT"):
            return ParsedQuery(sql, QueryKind.TRANSACTION, command=TransactionCommand.ABORT)
        if u.startswith("SELECT"):
            plan = Project(columns=[], children=[TableScan(table="student")], output_columns=[])
            return ParsedQuery(sql, QueryKind.SELECT, plan=plan, estimated_cost=0.0)
        raise QuerySyntaxError("mock: not supported", position=0, token=s.split()[0])

    def optimize_query(self, query: ParsedQuery) -> ParsedQuery:
        """Purpose: optimize. Mock: do nothing."""
        return query

    def get_cost(self, plan: PlanNode) -> float:
        """Purpose: estimate cost. Mock: always 0.0."""
        return 0.0

    def explain(self, query: ParsedQuery) -> str:
        """Purpose: show plan. Mock: return a short placeholder."""
        return "mock plan"
