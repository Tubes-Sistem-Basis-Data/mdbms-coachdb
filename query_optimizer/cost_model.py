"""Estimasi biaya plan dalam jumlah transfer blok."""

from __future__ import annotations

from common.plan import PlanNode


class CostModel:
    """Model biaya query plan berdasarkan statistik SM (QO-08)."""

    def estimate(self, plan: PlanNode) -> float:
        return 0.0


def estimate_cost(plan: PlanNode) -> float:
    """Hitung biaya satu plan."""
    return 0.0
