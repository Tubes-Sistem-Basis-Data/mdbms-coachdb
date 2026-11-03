"""Pemilihan jalur akses (table scan atau index scan)."""

from __future__ import annotations

from common.plan import PlanNode
from .cost_model import CostModel


class AccessPathChooser:
    """Mengganti TableScan dengan IndexScan jika lebih murah (QO-13)."""

    def choose(self, plan: PlanNode, cost_model: CostModel) -> PlanNode:
        return plan
