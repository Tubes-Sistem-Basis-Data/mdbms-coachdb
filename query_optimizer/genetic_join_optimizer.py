"""Optimasi urutan join dengan genetic algorithm (bonus QO-18).

Individu adalah permutasi daun join. Permutasi didekode menjadi join left-deep, lalu
setiap kondisi join dipasang di join terendah yang sudah memuat semua tabelnya.
Karena semua daun dan kondisi selalu dipakai tepat sekali, setiap individu valid.
"""

from __future__ import annotations

import copy
import random

from common.errors import SemanticError
from common.expression import And, BinaryOp, ColumnRef, Comparison, Expression
from common.plan import Join, JoinKind, PlanNode, Project
from .cost_model import CostModel
from .plan_utils import source_list

Individual = list[int]


def _is_join(node: PlanNode) -> bool:
    return isinstance(node, Join) and node.kind in (JoinKind.THETA, JoinKind.CROSS)


def _flatten_conditions(expr: Expression) -> list[Expression]:
    if isinstance(expr, And):
        result: list[Expression] = []
        for cond in expr.conditions:
            result.extend(_flatten_conditions(cond))
        return result
    return [expr]


def _expr_tables(expr: Expression) -> set[str] | None:
    """Tabel yang dirujuk kondisi. None jika ada kolom tanpa prefix (belum di-bind)."""
    if isinstance(expr, ColumnRef):
        return {expr.table} if expr.table else None
    if isinstance(expr, And):
        parts = [_expr_tables(cond) for cond in expr.conditions]
    elif isinstance(expr, (Comparison, BinaryOp)):
        parts = [_expr_tables(expr.left), _expr_tables(expr.right)]
    else:
        # Literal dan Subquery tak-berkorelasi tidak merujuk tabel luar
        return set()
    if any(part is None for part in parts):
        return None
    return set().union(*parts)


def _make_join(left: PlanNode, right: PlanNode, conditions: list[Expression], algorithm: str) -> Join:
    if not conditions:
        return Join(kind=JoinKind.CROSS, algorithm=algorithm, children=[left, right],
                    output_columns=left.output_columns + right.output_columns)
    condition = conditions[0] if len(conditions) == 1 else And(conditions=list(conditions))
    return Join(kind=JoinKind.THETA, condition=condition, algorithm=algorithm, children=[left, right],
                output_columns=left.output_columns + right.output_columns)


def order_crossover(parent1: Individual, parent2: Individual, rng: random.Random) -> Individual:
    """Order crossover (OX1): potongan parent1 dipertahankan, sisanya diisi urutan parent2."""
    n = len(parent1)
    if n < 2:
        return list(parent1)
    start, end = sorted(rng.sample(range(n), 2))
    child: list[int | None] = [None] * n
    child[start:end + 1] = parent1[start:end + 1]
    used = set(parent1[start:end + 1])
    rest = [gene for gene in parent2[end + 1:] + parent2[:end + 1] if gene not in used]
    positions = list(range(end + 1, n)) + list(range(0, start))
    for pos, gene in zip(positions, rest):
        child[pos] = gene
    return [gene for gene in child if gene is not None]


def swap_mutation(individual: Individual, rate: float, rng: random.Random) -> Individual:
    """Tukar dua posisi dengan peluang `rate`. Hasilnya tetap permutasi."""
    result = list(individual)
    if len(result) >= 2 and rng.random() < rate:
        i, j = rng.sample(range(len(result)), 2)
        result[i], result[j] = result[j], result[i]
    return result


def fitness(cost: float) -> float:
    """Fitness berbanding terbalik dengan biaya QO-08. Plan tidak valid bernilai 0."""
    if cost == float("inf"):
        return 0.0
    return 1.0 / (1.0 + cost)


class _JoinCluster:
    """Rangkaian join THETA/CROSS yang bisa diurutkan ulang: daun dan kondisinya."""

    def __init__(self, root: Join) -> None:
        self.algorithm = root.algorithm
        self.leaves: list[PlanNode] = []
        self.conditions: list[Expression] = []
        self._collect(root)
        self.leaf_tables = [set(source_list(leaf)) for leaf in self.leaves]
        self.condition_tables = [_expr_tables(cond) for cond in self.conditions]

    def _collect(self, node: PlanNode) -> None:
        if not _is_join(node):
            self.leaves.append(node)
            return
        for child in node.children:
            self._collect(child)
        if node.condition is not None:
            self.conditions.extend(_flatten_conditions(node.condition))

    def decode(self, order: Individual) -> PlanNode:
        """Bangun join left-deep sesuai urutan daun."""
        placed = [False] * len(self.conditions)
        current = self.leaves[order[0]]
        tables = set(self.leaf_tables[order[0]])
        for idx in order[1:]:
            tables |= self.leaf_tables[idx]
            ready = []
            for i, cond in enumerate(self.conditions):
                needed = self.condition_tables[i]
                if not placed[i] and needed and needed <= tables:
                    placed[i] = True
                    ready.append(cond)
            current = _make_join(current, self.leaves[idx], ready, self.algorithm)
        # kondisi tanpa tabel atau yang belum di-bind dipasang di join teratas
        leftover = [cond for i, cond in enumerate(self.conditions) if not placed[i]]
        if leftover:
            assert isinstance(current, Join)
            existing = _flatten_conditions(current.condition) if current.condition is not None else []
            left, right = current.children
            current = _make_join(left, right, existing + leftover, self.algorithm)
        return current


class GeneticJoinOptimizer:
    """Pencarian urutan join dengan genetic algorithm (QO-18).

    Dipanggil Planner jika mode pencarian join "ga". Fitness dihitung dari CostModel.
    """

    def __init__(
        self, population_size: int = 20, generations: int = 30, mutation_rate: float = 0.1,
        tournament_size: int = 3, seed: int | None = None,
    ) -> None:
        if type(population_size) is not int or population_size < 2:
            raise ValueError("population_size harus integer minimal 2")
        if type(generations) is not int or generations < 0:
            raise ValueError("generations harus integer tidak negatif")
        if not isinstance(mutation_rate, (int, float)) or not 0 <= mutation_rate <= 1:
            raise ValueError("mutation_rate harus di antara 0 dan 1")
        if type(tournament_size) is not int or tournament_size < 1:
            raise ValueError("tournament_size harus integer minimal 1")
        self._population_size = population_size
        self._generations = generations
        self._mutation_rate = float(mutation_rate)
        self._tournament_size = tournament_size
        self._seed = seed
        self.history: list[list[float]] = []

    def search(self, plan: PlanNode, cost_model: CostModel) -> PlanNode:
        """Kembalikan salinan plan dengan urutan join termurah yang ditemukan.

        Plan asli ikut dibandingkan, jadi hasilnya tidak pernah lebih mahal dari input.
        `history` berisi biaya terbaik per generasi untuk setiap rangkaian join.
        """
        self.history = []
        rng = random.Random(self._seed)
        return self._search(copy.deepcopy(plan), cost_model, rng, False)

    def _search(self, node: PlanNode, model: CostModel, rng: random.Random, preserve_order: bool) -> PlanNode:
        if isinstance(node, Project):
            # SELECT * tanpa daftar kolom: urutan kolom hasil ikut urutan join, jadi jangan diubah
            preserve_order = not node.columns
        if not _is_join(node):
            node.children = [self._search(child, model, rng, preserve_order) for child in node.children]
            return node

        cluster = _JoinCluster(node)
        # daun bisa berisi join lain (mis. di dalam subquery), optimasi dulu di tempat
        for leaf in cluster.leaves:
            leaf.children = [self._search(child, model, rng, preserve_order) for child in leaf.children]
        if preserve_order or len(cluster.leaves) < 2:
            return node

        best = self._evolve(cluster, model, rng)
        if _safe_cost(best, model) < _safe_cost(node, model) - 1e-9:
            return best
        return node

    def _evolve(self, cluster: _JoinCluster, model: CostModel, rng: random.Random) -> PlanNode:
        n = len(cluster.leaves)
        costs: dict[tuple[int, ...], float] = {}

        def cost_of(individual: Individual) -> float:
            key = tuple(individual)
            if key not in costs:
                costs[key] = _safe_cost(cluster.decode(individual), model)
            return costs[key]

        population = [list(range(n))]
        while len(population) < self._population_size:
            population.append(rng.sample(range(n), n))

        best = min(population, key=cost_of)
        history = [cost_of(best)]
        for _ in range(self._generations):
            # elitisme: individu terbaik selalu lolos ke generasi berikutnya
            next_population = [list(best)]
            while len(next_population) < self._population_size:
                parent1 = self._tournament(population, cost_of, rng)
                parent2 = self._tournament(population, cost_of, rng)
                child = order_crossover(parent1, parent2, rng)
                next_population.append(swap_mutation(child, self._mutation_rate, rng))
            population = next_population
            candidate = min(population, key=cost_of)
            if cost_of(candidate) < cost_of(best):
                best = candidate
            history.append(cost_of(best))
        self.history.append(history)
        return cluster.decode(best)

    def _tournament(self, population: list[Individual], cost_of, rng: random.Random) -> Individual:
        size = min(self._tournament_size, len(population))
        contestants = rng.sample(population, size)
        return max(contestants, key=lambda individual: fitness(cost_of(individual)))


def _safe_cost(node: PlanNode, model: CostModel) -> float:
    try:
        return model.get_cost(node)
    except SemanticError:
        return float("inf")
