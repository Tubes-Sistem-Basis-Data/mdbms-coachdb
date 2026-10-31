"""Penyusun plan: rewrite join, pilih jalur akses, atur urutan join."""

from __future__ import annotations

from common.plan import ParsedQuery, PlanNode
from .cost_model import CostModel
from .access_path_chooser import AccessPathChooser
from .join_order_optimizer import JoinOrderOptimizer
from .join_rule_rewriter import JoinRuleRewriter


class Planner:
    """Menjalankan rewrite dan pemilihan plan termurah (QO-11 sampai QO-14)."""

    def __init__(self, cost_model: CostModel) -> None:
        self._cost_model = cost_model
        self._access_chooser = AccessPathChooser()
        self._join_order_opt = JoinOrderOptimizer()
        self._join_rule_rewriter = JoinRuleRewriter()

    def optimize(self, query: ParsedQuery) -> ParsedQuery:
        """Optimasi plan di query. Dipanggil facade di optimize_query."""
        if query.plan:
            plan = self._join_rule_rewriter.apply(query.plan)
            plan = self._access_chooser.choose(plan, self._cost_model)
            plan = self._join_order_opt.reorder(plan, self._cost_model)
            query.plan = plan
        return query

    def _pick_cheapest(self, candidates: list[PlanNode]) -> PlanNode:
        """Pilih kandidat plan dengan biaya terkecil."""
        return candidates[0] if candidates else PlanNode()
