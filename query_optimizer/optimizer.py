"""Facade Query Optimizer: parse, validasi, optimasi, biaya, dan EXPLAIN."""

from __future__ import annotations

import copy
import logging

from common.errors import MdbmsError, QuerySyntaxError
from common.interfaces import QueryOptimizer, StorageManager
from common.plan import CreateIndex, Delete, Insert, ParsedQuery, PlanNode, Update
from query_optimizer.binder import Binder
from query_optimizer.cost_model import CostModel
from query_optimizer.join_rule_rewriter import JoinRuleRewriter
from query_optimizer.lexer import Lexer
from query_optimizer.parser_dispatcher import ParserDispatcher
from query_optimizer.plan_printer import PlanPrinter
from query_optimizer.planner import Planner
from query_optimizer.projection_rewriter import ProjectionRewriter
from query_optimizer.selection_rewriter import SelectionRewriter

logger = logging.getLogger(__name__)


class QueryOptimizerFacade(QueryOptimizer):
    """Pintu masuk QO untuk QP dan driver QO-16.

    parse_query: lexer -> parser -> binder (katalog dari SM).
    optimize_query: rewrite join, seleksi, proyeksi, lalu Planner (jalur akses dan urutan join).
    """

    def __init__(self, sm: StorageManager | None = None) -> None:
        self._catalog = sm
        self._lexer = Lexer()
        self._parser = ParserDispatcher()
        self._binder = Binder(sm)
        self._cost_model = CostModel(sm)
        self._join_rule = JoinRuleRewriter()
        self._selection = SelectionRewriter()
        self._projection = ProjectionRewriter()
        self._planner = Planner(self._cost_model)
        self._printer = PlanPrinter()

    def parse_query(self, sql: str) -> ParsedQuery:
        """Ubah teks SQL jadi ParsedQuery yang sudah divalidasi katalog. Dipanggil QP.

        Raise QuerySyntaxError atau SemanticError. Error lain dibungkus MdbmsError.
        """
        if not isinstance(sql, str):
            raise QuerySyntaxError("Query harus berupa teks", position=0, token="")
        try:
            parsed = self._parser.parse(self._lexer.tokenize(sql), sql)
            if parsed.plan is not None:
                self._binder.bind(parsed)
                parsed.estimated_cost = self._plan_cost(parsed.plan)
            return parsed
        except MdbmsError:
            raise
        except RecursionError as exc:
            raise QuerySyntaxError("Query terlalu dalam untuk diproses", position=0, token="") from exc
        except Exception as exc:
            logger.exception("kesalahan internal optimizer saat parse")
            raise MdbmsError(f"kesalahan internal optimizer: {type(exc).__name__}: {exc}") from exc

    def optimize_query(self, query: ParsedQuery) -> ParsedQuery:
        """Optimasi plan hasil parse_query. Dipanggil QP sebelum eksekusi.

        Plan hasil optimasi divalidasi ulang. Kalau rewrite menghasilkan plan yang tidak
        valid, plan asli yang dipakai supaya query tetap bisa dieksekusi.
        """
        if query.plan is None:
            return query
        original = query.plan
        try:
            plan = self._rewrite(copy.deepcopy(original))
            query.plan = plan
            query = self._planner.optimize(query)
            self._binder.bind(query)
        except Exception:
            logger.warning("optimasi gagal, memakai plan tanpa optimasi", exc_info=True)
            query.plan = original
        query.estimated_cost = self._plan_cost(query.plan)
        return query

    def get_cost(self, plan: PlanNode) -> float:
        """Hitung biaya plan dalam transfer blok. Untuk INSERT/UPDATE/DELETE yang dihitung sumber barisnya."""
        return self._plan_cost(plan)

    def explain(self, query: ParsedQuery) -> str:
        """Tampilkan plan sebelum dan sesudah optimasi. Tidak menyentuh CCM dan tidak mengeksekusi apa pun."""
        before = query.plan
        cost_before = self._plan_cost(before) if before is not None else 0.0
        # optimasi salinan supaya plan asli tidak ikut berubah
        optimized = self.optimize_query(copy.deepcopy(query))
        after = optimized.plan
        cost_after = self._plan_cost(after) if after is not None else 0.0
        return self._printer.build_explain(before, after, cost_before, cost_after)

    def _rewrite(self, plan: PlanNode) -> PlanNode:
        if isinstance(plan, (Update, Delete)):
            # sumber UPDATE/DELETE harus tetap scan tabel target dengan Filter, jadi proyeksi tidak dipakai
            plan.children = [self._selection.apply(child) for child in plan.children]
            return plan
        if isinstance(plan, (Insert, CreateIndex)):
            return plan
        # join rule lebih dulu supaya kondisi antar tabel jadi kondisi join sebelum seleksi didorong
        plan = self._join_rule.apply(plan)
        plan = self._selection.apply(plan)
        return self._projection.apply(plan)

    def _plan_cost(self, plan: PlanNode) -> float:
        if isinstance(plan, (Insert, CreateIndex)):
            return 0.0
        if isinstance(plan, (Update, Delete)):
            return sum(self._cost_model.get_cost(child) for child in plan.children)
        return self._cost_model.get_cost(plan)
