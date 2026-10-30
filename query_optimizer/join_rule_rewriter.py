"""QO-11: Mengubah Cartesian Product menjadi Join.

PIC: QO5 (Dzakwan)
Tanggung jawab:
- Mendeteksi pola Filter di atas Join(CROSS) yang kondisinya menghubungkan
  kolom dari kedua sisi join.
- Mengubahnya menjadi Join(THETA) dengan kondisi tersebut.
- Jika kondisi AND memuat campuran predikat join dan predikat lokal,
  predikat join dipindahkan ke kondisi Join, sisanya tetap di Filter.
"""

from __future__ import annotations

import copy
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
    pass


class JoinRuleRewriter:
    """Mengubah Cartesian Product (CROSS join) menjadi Theta Join.

    Aturan:
    - Filter(cond, Join(CROSS, left, right))
      → Join(THETA, cond, left, right) jika cond menghubungkan kedua sisi.
    - Jika cond berisi AND campuran (join predicate + local predicate),
      pisahkan: join predicate masuk ke Join, local predicate tetap di Filter.

    Pipeline Planner memanggil `apply()` setelah SelectionRewriter dan
    ProjectionRewriter, sebelum AccessPathChooser dan JoinOrderOptimizer.
    """

    def apply(self, plan: PlanNode) -> PlanNode:
        """Jalankan transformasi Cartesian → Join secara rekursif bottom-up."""
        return self._transform(plan)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _transform(self, node: PlanNode) -> PlanNode:
        """Traversal bottom-up: transformasi anak dulu, lalu node ini."""
        # Rekursif ke anak-anak terlebih dahulu
        if node.children:
            new_children: list[PlanNode] = []
            for child in node.children:
                new_children.append(self._transform(child))
            node.children = new_children

        # Pola: Filter di atas Join(CROSS)
        if isinstance(node, Filter) and node.children:
            child = node.children[0]
            if isinstance(child, Join) and child.kind == JoinKind.CROSS:
                return self._try_convert(node, child)

        return node

    def _try_convert(self, filt: Filter, join: Join) -> PlanNode:
        """Coba konversi Filter(cond) + Join(CROSS) → Join(THETA, cond).

        Jika semua predikat adalah join predicate → hapus Filter.
        Jika campuran → split, join predicate ke Join, sisanya tetap di Filter.
        Jika tidak ada join predicate → kembalikan tanpa perubahan.
        """
        predicates = self._flatten_and(filt.condition)

        left_tables = self._collect_tables(join.children[0]) if len(join.children) > 0 else set()
        right_tables = self._collect_tables(join.children[1]) if len(join.children) > 1 else set()

        join_preds: list[Expression] = []
        local_preds: list[Expression] = []

        for pred in predicates:
            tables_in_pred = self._extract_tables(pred)
            # Predikat join: menyentuh kedua sisi
            if tables_in_pred & left_tables and tables_in_pred & right_tables:
                join_preds.append(pred)
            else:
                local_preds.append(pred)

        if not join_preds:
            # Tidak ada join predicate, biarkan apa adanya
            return filt

        # Bangun kondisi join
        join_cond = self._build_conjunction(join_preds)

        # Buat Join(THETA) baru
        new_join = Join(
            kind=JoinKind.THETA,
            condition=join_cond,
            algorithm=join.algorithm,
            children=join.children,
            output_columns=join.output_columns,
            estimated_rows=join.estimated_rows,
            estimated_cost=join.estimated_cost,
        )

        if not local_preds:
            # Semua predikat sudah dipindahkan ke join
            return new_join

        # Masih ada predikat lokal → bungkus dengan Filter
        remaining_cond = self._build_conjunction(local_preds)
        return Filter(
            condition=remaining_cond,
            children=[new_join],
            output_columns=filt.output_columns,
            estimated_rows=filt.estimated_rows,
            estimated_cost=filt.estimated_cost,
        )

    def _flatten_and(self, expr: Expression) -> list[Expression]:
        """Pecah And(conditions) menjadi daftar datar."""
        if isinstance(expr, And):
            result: list[Expression] = []
            for cond in expr.conditions:
                result.extend(self._flatten_and(cond))
            return result
        return [expr]

    def _build_conjunction(self, preds: list[Expression]) -> Expression:
        """Gabung daftar predikat menjadi satu Expression (And jika > 1)."""
        if len(preds) == 1:
            return preds[0]
        return And(conditions=preds)

    def _collect_tables(self, node: PlanNode) -> set[str]:
        """Kumpulkan semua nama tabel yang menjadi sumber data di bawah node."""
        tables: set[str] = set()
        if isinstance(node, TableScan):
            tables.add(node.table)
        elif isinstance(node, Join):
            for child in node.children:
                tables.update(self._collect_tables(child))
        elif hasattr(node, 'table'):
            tables.add(node.table)
        # Rekursif ke children
        for child in node.children:
            tables.update(self._collect_tables(child))
        return tables

    def _extract_tables(self, expr: Expression) -> set[str]:
        """Ekstrak nama tabel yang direferensikan dalam ekspresi."""
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

