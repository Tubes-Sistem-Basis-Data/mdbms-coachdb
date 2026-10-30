from __future__ import annotations

from dataclasses import dataclass

from common.interfaces import StorageManager
from common.plan import PlanNode


@dataclass
class Estimate:
    rows: float
    cost: float


class CostModel:
    # Store the catalog connection
    def __init__(self, sm: StorageManager) -> None:
        self._sm = sm

    # Get the total cost of the plan
    def get_cost(self, plan: PlanNode) -> float:
        return 0.0

    # Estimate the row count and cost of the plan
    def estimate(self, plan: PlanNode) -> Estimate:
        return Estimate(rows=0.0, cost=0.0)
