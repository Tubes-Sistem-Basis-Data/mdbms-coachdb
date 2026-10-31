"""Purpose: rewrite joins. Mock: do nothing."""

from __future__ import annotations

from common.plan import PlanNode


class JoinRuleRewriter:
    def apply(self, plan: PlanNode) -> PlanNode:
        return plan
