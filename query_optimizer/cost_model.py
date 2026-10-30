"""Purpose: estimate cost. Mock: always 0.0."""

from __future__ import annotations

from common.plan import PlanNode


def estimate_cost(plan: PlanNode) -> float:
    """Compute query cost"""
    return 0.0
