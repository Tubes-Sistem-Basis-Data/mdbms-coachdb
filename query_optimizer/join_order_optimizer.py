"""QO-12: Menukar Urutan dan Pengelompokan Join.

PIC: QO5 (Dzakwan)
Tanggung jawab:
- Mengeksplorasi urutan join yang berbeda (commutativity dan associativity).
- Menggunakan CostModel untuk membandingkan biaya masing-masing permutasi.
- Untuk jumlah tabel kecil (≤ 6): dynamic programming exhaustive.
- Untuk jumlah tabel lebih besar: greedy heuristik.
"""

from __future__ import annotations

import itertools
import math
from typing import TYPE_CHECKING

from common.expression import And, ColumnRef, Comparison, Expression
from common.plan import (
    Filter,
    Join,
    JoinKind,
    PlanNode,
    TableScan,
)

if TYPE_CHECKING:
    from query_optimizer.cost_model import CostModel


# Batas jumlah tabel untuk exhaustive enumeration
_EXHAUSTIVE_THRESHOLD = 6


class JoinOrderOptimizer:
    """Menentukan urutan join yang optimal berdasarkan biaya.

    Strategi:
    - Commutativity: swap(Join(A, B)) → Join(B, A)
    - Associativity: Join(Join(A, B), C) → Join(A, Join(B, C))
    - Untuk ≤ 6 tabel: enumerasi semua permutasi (brute-force / DP).
    - Untuk > 6 tabel: greedy, mulai dari 2 tabel termurah, gabung bertahap.

    Pipeline Planner memanggil `reorder()` setelah AccessPathChooser.
    """

    def reorder(self, plan: PlanNode, cost_model: CostModel) -> PlanNode:
        """Cari urutan join terbaik untuk plan yang mengandung multi-join.

        Jika plan bukan join atau hanya satu join, kembalikan apa adanya.
        """
        # Kumpulkan semua relasi daun dan predikat join
        relations, predicates = self._decompose_joins(plan)

        if len(relations) <= 1:
            # Tidak ada join yang bisa di-reorder
            return plan

        if len(relations) <= _EXHAUSTIVE_THRESHOLD:
            best_plan = self._exhaustive_search(relations, predicates, cost_model)
        else:
            best_plan = self._greedy_search(relations, predicates, cost_model)

        if best_plan is not None:
            # Pertahankan node-node di atas join chain (Filter, Project, Sort, dll)
            return self._replace_join_subtree(plan, best_plan)

        return plan

    # ------------------------------------------------------------------
    # Decompose: Kumpulkan relasi daun dan predikat dari join tree
    # ------------------------------------------------------------------

    def _decompose_joins(
        self, node: PlanNode
    ) -> tuple[list[PlanNode], list[Expression]]:
        """Pecahkan tree join menjadi daftar relasi daun dan predikat.

        Hanya memecah node Join. Node lain (Filter, Project, Sort, dsb.)
        dianggap sebagai 'atap' dan tidak diikutsertakan dalam reordering.
        """
        if isinstance(node, Join):
            left_rels, left_preds = self._decompose_joins(
                node.children[0] if len(node.children) > 0 else node
            )
            right_rels, right_preds = self._decompose_joins(
                node.children[1] if len(node.children) > 1 else node
            )

            preds = left_preds + right_preds
            if node.condition is not None:
                preds.extend(self._flatten_and(node.condition))

            return left_rels + right_rels, preds

        # Bukan Join → ini adalah relasi daun
        return [node], []

    # ------------------------------------------------------------------
    # Exhaustive search (untuk ≤ 6 tabel)
    # ------------------------------------------------------------------

    def _exhaustive_search(
        self,
        relations: list[PlanNode],
        predicates: list[Expression],
        cost_model: CostModel,
    ) -> PlanNode | None:
        """Enumerasi semua permutasi dan pilih yang berbiaya terendah."""
        best_plan: PlanNode | None = None
        best_cost = math.inf

        for perm in itertools.permutations(range(len(relations))):
            candidate = self._build_left_deep(
                [relations[i] for i in perm], predicates
            )
            cost = cost_model.get_cost(candidate)
            if cost < best_cost:
                best_cost = cost
                best_plan = candidate

        return best_plan

    # ------------------------------------------------------------------
    # Greedy search (untuk > 6 tabel)
    # ------------------------------------------------------------------

    def _greedy_search(
        self,
        relations: list[PlanNode],
        predicates: list[Expression],
        cost_model: CostModel,
    ) -> PlanNode | None:
        """Greedy: setiap langkah gabungkan pasangan termurah."""
        remaining = list(relations)

        while len(remaining) > 1:
            best_i, best_j = 0, 1
            best_cost = math.inf
            best_join: PlanNode | None = None

            for i in range(len(remaining)):
                for j in range(i + 1, len(remaining)):
                    candidate = self._make_join(
                        remaining[i], remaining[j], predicates
                    )
                    cost = cost_model.get_cost(candidate)
                    if cost < best_cost:
                        best_cost = cost
                        best_i, best_j = i, j
                        best_join = candidate

            if best_join is None:
                break

            # Hapus j dulu (index lebih besar) supaya i tidak bergeser
            remaining.pop(best_j)
            remaining.pop(best_i)
            remaining.append(best_join)

        return remaining[0] if remaining else None

    # ------------------------------------------------------------------
    # Helper: bangun join tree
    # ------------------------------------------------------------------

    def _build_left_deep(
        self, relations: list[PlanNode], predicates: list[Expression]
    ) -> PlanNode:
        """Bangun left-deep join tree dari daftar relasi dan predikat."""
        result = relations[0]

        for i in range(1, len(relations)):
            result = self._make_join(result, relations[i], predicates)

        return result

    def _make_join(
        self,
        left: PlanNode,
        right: PlanNode,
        predicates: list[Expression],
    ) -> PlanNode:
        """Buat Join node antara left dan right, pilih predikat yang cocok."""
        left_tables = self._collect_tables(left)
        right_tables = self._collect_tables(right)

        applicable: list[Expression] = []
        for pred in predicates:
            tables_in_pred = self._extract_tables(pred)
            # Predikat berlaku jika semua tabelnya ada di left ∪ right
            if tables_in_pred and tables_in_pred <= (left_tables | right_tables):
                # Dan menyentuh kedua sisi (join predicate, bukan local)
                if tables_in_pred & left_tables and tables_in_pred & right_tables:
                    applicable.append(pred)

        if applicable:
            cond = applicable[0] if len(applicable) == 1 else And(conditions=applicable)
            kind = JoinKind.THETA
        else:
            cond = None
            kind = JoinKind.CROSS

        return Join(
            kind=kind,
            condition=cond,
            algorithm="BNLJ",
            children=[left, right],
        )

    # ------------------------------------------------------------------
    # Replace join subtree di dalam plan tree
    # ------------------------------------------------------------------

    def _replace_join_subtree(self, original: PlanNode, new_join: PlanNode) -> PlanNode:
        """Ganti subtree join terdalam di original dengan new_join.

        Pertahankan node-node di atas join chain (Filter, Project, Sort).
        """
        if isinstance(original, Join):
            return new_join

        # Node non-join: ganti child yang mengandung join
        if original.children:
            new_children: list[PlanNode] = []
            for child in original.children:
                new_children.append(self._replace_join_subtree(child, new_join))
            original.children = new_children

        return original

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def _flatten_and(self, expr: Expression) -> list[Expression]:
        """Pecah And menjadi daftar datar."""
        if isinstance(expr, And):
            result: list[Expression] = []
            for cond in expr.conditions:
                result.extend(self._flatten_and(cond))
            return result
        return [expr]

    def _collect_tables(self, node: PlanNode) -> set[str]:
        """Kumpulkan nama tabel dari semua daun di bawah node."""
        tables: set[str] = set()
        if isinstance(node, TableScan):
            tables.add(node.table)
        elif hasattr(node, 'table') and not isinstance(node, Join):
            tables.add(node.table)
        for child in node.children:
            tables.update(self._collect_tables(child))
        return tables

    def _extract_tables(self, expr: Expression) -> set[str]:
        """Ekstrak nama tabel dari ekspresi."""
        tables: set[str] = set()
        if isinstance(expr, ColumnRef):
            if expr.table:
                tables.add(expr.table)
        elif isinstance(expr, Comparison):
            tables.update(self._extract_tables(expr.left))
            tables.update(self._extract_tables(expr.right))
        elif isinstance(expr, And):
            for cond in expr.conditions:
                tables.update(self._extract_tables(cond))
        return tables

