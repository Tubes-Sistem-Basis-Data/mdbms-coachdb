"""Purpose: choose access path. Mock: always use sequential scan."""

from __future__ import annotations

from common.plan import PlanNode
from .cost_model import CostModel


class AccessPathChooser:
    def choose(self, plan: PlanNode, cost_model: CostModel) -> PlanNode:
        return plan
