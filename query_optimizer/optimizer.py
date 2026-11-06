"""Facade QO sementara untuk Milestone 1."""

from __future__ import annotations

from common.errors import QuerySyntaxError
from common.interfaces import QueryOptimizer, StorageManager
from common.plan import ParsedQuery, PlanNode, Project, QueryKind, TableScan, TransactionCommand
from query_optimizer.binder import Binder
from query_optimizer.cost_model import CostModel
from query_optimizer.lexer import Lexer
from query_optimizer.parser_dispatcher import ParserDispatcher
from query_optimizer.plan_printer import PlanPrinter
from query_optimizer.planner import Planner


class QueryOptimizerFacade(QueryOptimizer):
    """Facade QO versi awal, hanya mengenali BEGIN, COMMIT, ABORT, dan SELECT."""

    def __init__(self, sm: StorageManager | None = None) -> None:
        self._catalog = sm
        self._lexer = Lexer()
        self._parser = ParserDispatcher()
        self._binder = Binder(sm)
        self._cost_model = CostModel(sm)
        self._planner = Planner(self._cost_model)
        self._printer = PlanPrinter()

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
