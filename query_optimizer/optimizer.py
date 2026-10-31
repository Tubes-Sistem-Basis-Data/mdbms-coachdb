"""Facade QO sementara untuk Milestone 1."""

from __future__ import annotations

from common.errors import QuerySyntaxError
from common.interfaces import QueryOptimizer
from common.plan import ParsedQuery, PlanNode, Project, QueryKind, TableScan, TransactionCommand


class MockQueryOptimizer(QueryOptimizer):
    """Facade QO versi awal, hanya mengenali BEGIN, COMMIT, ABORT, dan SELECT."""

    def parse_query(self, sql: str) -> ParsedQuery:
        """Ubah teks SQL jadi ParsedQuery. Dipanggil QP."""
        s = sql.strip().rstrip(";")
        if not s:
            raise QuerySyntaxError("mock: query kosong", position=0, token="")
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
        raise QuerySyntaxError("mock: belum didukung", position=0, token=s.split()[0])

    def optimize_query(self, query: ParsedQuery) -> ParsedQuery:
        """Optimasi plan. Dipanggil QP setelah parse_query."""
        return query

    def get_cost(self, plan: PlanNode) -> float:
        """Hitung biaya plan dalam transfer blok."""
        return 0.0

    def explain(self, query: ParsedQuery) -> str:
        """Tampilkan plan untuk perintah EXPLAIN."""
        return "mock plan"
