"""QO-18* (Bonus): Optimasi Urutan Join dengan Algoritma Genetik.

PIC: QO5 (Dzakwan)
Tanggung jawab:
- Algoritma evolusioner untuk kasus join ≥ 7 tabel.
- Representasi kromosom: permutasi indeks relasi (left-deep tree).
- Operator: seleksi turnamen, crossover OX (Order Crossover), mutasi swap.
- Fitness: kebalikan biaya (semakin murah, semakin fit).
"""

from __future__ import annotations

import math
import random
from typing import TYPE_CHECKING

from common.expression import And, ColumnRef, Comparison, Expression
from common.plan import Join, JoinKind, PlanNode, TableScan

if TYPE_CHECKING:
    from query_optimizer.cost_model import CostModel


class GeneticJoinOptimizer:
    """Mencari urutan join terbaik dengan algoritma genetik.

    Digunakan oleh Planner sebagai fallback ketika jumlah tabel > 6
    (menggantikan greedy JoinOrderOptimizer).

    Kromosom: list[int] — permutasi [0..n-1] indeks relasi.
    Fitness: 1 / (cost + ε) — semakin rendah biaya, semakin tinggi fitness.
    """

    _EPSILON = 1e-9  # mencegah bagi nol saat cost sangat kecil

    def __init__(
        self,
        population_size: int = 50,
        generations: int = 100,
        mutation_rate: float = 0.1,
        seed: int | None = None,
    ) -> None:
        """Inisialisasi parameter genetik.

        Args:
            population_size: Jumlah individu per generasi.
            generations: Jumlah generasi yang dijalankan.
            mutation_rate: Probabilitas mutasi per kromosom [0, 1].
            seed: Seed random untuk reproducibility (opsional).
        """
        self._population_size = population_size
        self._generations = generations
        self._mutation_rate = mutation_rate
        if seed is not None:
            random.seed(seed)

    def search(self, plan: PlanNode, cost_model: CostModel) -> PlanNode:
        """Cari urutan join terbaik untuk plan yang mengandung multi-join.

        Args:
            plan: PlanNode berisi join tree yang akan dioptimalkan.
            cost_model: Digunakan untuk menghitung fitness setiap kandidat.

        Returns:
            PlanNode dengan urutan join terbaik yang ditemukan.
        """
        from query_optimizer.join_order_optimizer import JoinOrderOptimizer

        helper = JoinOrderOptimizer()
        relations, predicates = helper._decompose_joins(plan)

        n = len(relations)
        if n <= 1:
            return plan

        # Inisialisasi populasi awal
        population = self._init_population(n)

        best_chromosome: list[int] = population[0]
        best_cost = math.inf

        for _ in range(self._generations):
            # Evaluasi fitness seluruh populasi
            costs = [
                cost_model.get_cost(
                    helper._build_left_deep(
                        [relations[i] for i in chrom], predicates
                    )
                )
                for chrom in population
            ]

            # Update individu terbaik global
            min_idx = min(range(n), key=lambda i: costs[i])
            if costs[min_idx] < best_cost:
                best_cost = costs[min_idx]
                best_chromosome = population[min_idx][:]

            # Bangun generasi berikutnya
            next_pop: list[list[int]] = [best_chromosome[:]]  # elitisme

            while len(next_pop) < self._population_size:
                parent_a = self._tournament_select(population, costs)
                parent_b = self._tournament_select(population, costs)
                child = self._order_crossover(parent_a, parent_b)
                if random.random() < self._mutation_rate:
                    self._mutate(child)
                next_pop.append(child)

            population = next_pop

        best_plan = helper._build_left_deep(
            [relations[i] for i in best_chromosome], predicates
        )
        return helper._replace_join_subtree(plan, best_plan)

    # ------------------------------------------------------------------
    # Inisialisasi populasi
    # ------------------------------------------------------------------

    def _init_population(self, n: int) -> list[list[int]]:
        """Buat populasi awal dari permutasi acak."""
        base = list(range(n))
        population: list[list[int]] = []
        for _ in range(self._population_size):
            chrom = base[:]
            random.shuffle(chrom)
            population.append(chrom)
        return population

    # ------------------------------------------------------------------
    # Seleksi turnamen
    # ------------------------------------------------------------------

    def _tournament_select(
        self,
        population: list[list[int]],
        costs: list[float],
        tournament_size: int = 3,
    ) -> list[int]:
        """Pilih individu terbaik dari subset acak (tournament selection)."""
        candidates = random.sample(range(len(population)), min(tournament_size, len(population)))
        winner = min(candidates, key=lambda i: costs[i])
        return population[winner][:]

    # ------------------------------------------------------------------
    # Order Crossover (OX)
    # ------------------------------------------------------------------

    def _order_crossover(
        self, parent_a: list[int], parent_b: list[int]
    ) -> list[int]:
        """Order Crossover: pertahankan segmen dari parent_a, isi sisa dari parent_b."""
        n = len(parent_a)
        if n <= 2:
            return parent_a[:]

        i, j = sorted(random.sample(range(n), 2))
        child: list[int] = [-1] * n
        child[i : j + 1] = parent_a[i : j + 1]

        segment_set = set(child[i : j + 1])
        fill_pos = (j + 1) % n
        for gene in parent_b:
            if gene not in segment_set:
                child[fill_pos] = gene
                fill_pos = (fill_pos + 1) % n

        return child

    # ------------------------------------------------------------------
    # Mutasi swap
    # ------------------------------------------------------------------

    def _mutate(self, chromosome: list[int]) -> None:
        """Swap dua gen acak (in-place)."""
        n = len(chromosome)
        if n < 2:
            return
        i, j = random.sample(range(n), 2)
        chromosome[i], chromosome[j] = chromosome[j], chromosome[i]

