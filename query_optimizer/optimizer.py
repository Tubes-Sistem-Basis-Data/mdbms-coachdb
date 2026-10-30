"""Facade utama Query Optimizer — QueryOptimizerImpl.

PIC: QO5 (Dzakwan) — koordinasi pipeline & facade
     QO1 (Angelina) — parse_query, explain, plan printer

Implementasi `QueryOptimizer` (common/interfaces/query_optimizer.py).
Pipeline: tokenize → parse → bind → rewrite → cost → plan termurah.

Catatan desain:
- Facade memakai constructor injection: semua dependensi disuntikkan dari
  luar (biasanya oleh main.py atau driver), bukan dibuat sendiri.
- `parse_query` memanggil Lexer, ParserDispatcher, Binder.
- `optimize_query` memanggil Planner (yang mengkoordinasi semua rewriter).
- `explain` mengembalikan string teks untuk perintah EXPLAIN.
- `get_cost` meneruskan ke CostModel.
"""

from __future__ import annotations

from common.interfaces import StorageManager
from common.interfaces.query_optimizer import QueryOptimizer
from common.plan import ParsedQuery, PlanNode

from query_optimizer.access_path_chooser import AccessPathChooser
from query_optimizer.binder import Binder
from query_optimizer.cost_model import CostModel
from query_optimizer.join_order_optimizer import JoinOrderOptimizer
from query_optimizer.join_rule_rewriter import JoinRuleRewriter
from query_optimizer.lexer import Lexer
from query_optimizer.parser import ParserDispatcher, SelectParser
from query_optimizer.expression_parser import ExpressionParser
from query_optimizer.statement_parser import StatementParser
from query_optimizer.plan_printer import PlanPrinter
from query_optimizer.planner import Planner
from query_optimizer.projection_rewriter import ProjectionRewriter
from query_optimizer.selection_rewriter import SelectionRewriter


class QueryOptimizerImpl(QueryOptimizer):
    """Implementasi facade Query Optimizer.

    Semua komponen internal dibuat di konstruktor dan dapat di-override
    untuk keperluan pengujian (dependency injection).

    Args:
        sm: StorageManager untuk katalog dan statistik.
    """

    def __init__(self, sm: StorageManager) -> None:
        self._sm = sm

        # --- Zona 1: Frontend ---
        self._lexer = Lexer()
        expr_parser = ExpressionParser()
        stmt_parser = StatementParser(expr_parser)
        select_parser = SelectParser(expr_parser)
        self._parser = ParserDispatcher(select_parser, stmt_parser)

        # --- Zona 2: Binder & CostModel ---
        self._binder = Binder(sm)
        self._cost_model = CostModel(sm)

        # --- Zona 3: Rewriters & Planner ---
        sel_rewriter = SelectionRewriter()
        proj_rewriter = ProjectionRewriter()
        join_rewriter = JoinRuleRewriter()
        access_chooser = AccessPathChooser(sm)
        join_order_opt = JoinOrderOptimizer()

        self._planner = Planner(
            cost_model=self._cost_model,
            sel_rewriter=sel_rewriter,
            proj_rewriter=proj_rewriter,
            join_rewriter=join_rewriter,
            access_chooser=access_chooser,
            join_order_opt=join_order_opt,
        )

        # --- Printer ---
        self._printer = PlanPrinter()

    # ------------------------------------------------------------------
    # Implementasi kontrak QueryOptimizer (K1)
    # ------------------------------------------------------------------

    def parse_query(self, sql: str) -> ParsedQuery:
        """Tokenisasi, parsing, dan binding query SQL.

        Pipeline: Lexer → ParserDispatcher → Binder.

        Raises:
            QuerySyntaxError: Jika SQL memiliki sintaks tidak valid.
            SemanticError: Jika tabel/kolom tidak ada di katalog.
        """
        tokens = self._lexer.tokenize(sql)
        parsed = self._parser.parse(tokens, sql)
        bound = self._binder.bind(parsed)
        return bound

    def optimize_query(self, query: ParsedQuery) -> ParsedQuery:
        """Optimalkan query; kembalikan ParsedQuery dengan plan terbaik.

        Pipeline: SelectionRewriter → ProjectionRewriter → JoinRuleRewriter
                  → AccessPathChooser → JoinOrderOptimizer → pick_cheapest.

        ParsedQuery yang dikembalikan memiliki `estimated_cost` terisi.
        """
        return self._planner.optimize(query)

    def get_cost(self, plan: PlanNode) -> float:
        """Estimasi biaya plan dalam satuan transfer blok."""
        return self._cost_model.get_cost(plan)

    def explain(self, query: ParsedQuery) -> str:
        """Kembalikan teks plan sebelum dan sesudah optimasi beserta biaya.

        Perintah EXPLAIN tidak berinteraksi dengan CCM.
        Plan yang sudah di-bind akan dioptimasi secara simulasi.

        Returns:
            String teks yang memuat plan awal, plan optimal, dan biaya.
        """
        # Plan awal (setelah bind, sebelum optimasi)
        plan_before = query.plan
        cost_before = self._cost_model.get_cost(plan_before) if plan_before else 0.0

        # Optimasi simulasi (tidak mengubah query asli)
        import copy as _copy
        query_copy = _copy.deepcopy(query)
        optimized = self._planner.optimize(query_copy)

        plan_after = optimized.plan
        cost_after = optimized.estimated_cost

        return self._printer.build_explain(plan_before, plan_after, cost_before, cost_after)

