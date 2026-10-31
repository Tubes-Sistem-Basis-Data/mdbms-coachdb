"""Optimasi urutan join dengan genetic algorithm (bonus QO-18)."""

from __future__ import annotations

from common.plan import PlanNode
from .cost_model import CostModel


class GeneticJoinOptimizer:
    def __init__(self, population_size: int = 10, generations: int = 5, mutation_rate: float = 0.1):
        self._population_size = population_size
        self._generations = generations
        self._mutation_rate = mutation_rate

    def search(self, plan: PlanNode, cost_model: CostModel) -> PlanNode:
        return plan
