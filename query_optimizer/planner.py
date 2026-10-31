"""Penyusun plan: rewrite join, pilih jalur akses, atur urutan join."""

from __future__ import annotations

from common.plan import ParsedQuery
from .cost_model import CostModel
from .access_path_chooser import AccessPathChooser
from .join_order_optimizer import JoinOrderOptimizer
from .join_rule_rewriter import JoinRuleRewriter


class Planner:
    def __init__(self, cost_model: CostModel):
        self._cost_model = cost_model
        self._access_chooser = AccessPathChooser()
        self._join_order_opt = JoinOrderOptimizer()
        self._join_rule_rewriter = JoinRuleRewriter()

    def optimize(self, query: ParsedQuery) -> ParsedQuery:
        if query.plan:
            plan = self._join_rule_rewriter.apply(query.plan)
            plan = self._access_chooser.choose(plan, self._cost_model)
            plan = self._join_order_opt.reorder(plan, self._cost_model)
            query.plan = plan
        return query
