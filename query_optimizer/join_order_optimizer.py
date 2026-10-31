"""Penukaran urutan dan pengelompokan join (QO-12)."""

from __future__ import annotations

from common.plan import PlanNode
from .cost_model import CostModel


class JoinOrderOptimizer:
    def reorder(self, plan: PlanNode, cost_model: CostModel) -> PlanNode:
        return plan
