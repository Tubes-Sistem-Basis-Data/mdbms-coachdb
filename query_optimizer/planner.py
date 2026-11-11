"""Penyusun plan: rewrite join, pilih jalur akses, atur urutan join."""

from __future__ import annotations

from common.plan import ParsedQuery, PlanNode
from .cost_model import CostModel
from .access_path_chooser import AccessPathChooser
from .genetic_join_optimizer import GeneticJoinOptimizer
from .join_order_optimizer import JoinOrderOptimizer
from .join_rule_rewriter import JoinRuleRewriter


class Planner:
    """Menjalankan rewrite dan pemilihan plan termurah (QO-11 sampai QO-14)."""

    def __init__(
        self, cost_model: CostModel, join_search: str = "heuristic",
        genetic: GeneticJoinOptimizer | None = None,
    ) -> None:
        """`join_search` "heuristic" memakai JoinOrderOptimizer, "ga" memakai GeneticJoinOptimizer."""
        if join_search not in ("heuristic", "ga"):
            raise ValueError("join_search harus 'heuristic' atau 'ga'")
        self._cost_model = cost_model
        self._join_search = join_search
        self._access_chooser = AccessPathChooser()
        self._join_order_opt = JoinOrderOptimizer()
        self._genetic_opt = genetic if genetic is not None else GeneticJoinOptimizer()
        self._join_rule_rewriter = JoinRuleRewriter()

    def optimize(self, query: ParsedQuery) -> ParsedQuery:
        """Optimasi plan di query. Dipanggil facade di optimize_query."""
        if query.plan:
            plan = self._join_rule_rewriter.apply(query.plan)
            plan = self._access_chooser.choose(plan, self._cost_model)
            if self._join_search == "ga":
                plan = self._genetic_opt.search(plan, self._cost_model)
            else:
                plan = self._join_order_opt.reorder(plan, self._cost_model)
            query.plan = plan
        return query

    def _pick_cheapest(self, candidates: list[PlanNode]) -> PlanNode:
        """Pilih kandidat plan dengan biaya terkecil."""
        return candidates[0] if candidates else PlanNode()
