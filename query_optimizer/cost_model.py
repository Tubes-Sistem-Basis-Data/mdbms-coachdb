"""Purpose: estimate cost. Mock: always 0.0."""

from __future__ import annotations

from common.plan import PlanNode


class CostModel:
    """Purpose: model biaya query plan. Mock: selalu 0.0."""

    def estimate(self, plan: PlanNode) -> float:
        return 0.0


def estimate_cost(plan: PlanNode) -> float:
    """Compute query cost"""
    return 0.0
