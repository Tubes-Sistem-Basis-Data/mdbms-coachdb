"""Mengubah cartesian product dengan seleksi menjadi join (QO-11)."""

from __future__ import annotations

from common.plan import PlanNode


class JoinRuleRewriter:
    def apply(self, plan: PlanNode) -> PlanNode:
        return plan
