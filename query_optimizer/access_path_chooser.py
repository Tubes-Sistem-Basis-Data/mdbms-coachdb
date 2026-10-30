"""QO-13: Memanfaatkan Index (Index Scan).

PIC: QO5 (Dzakwan)
Tanggung jawab:
- Memeriksa apakah ada index pada kolom yang digunakan di kondisi Filter.
- Jika ya, membandingkan biaya TableScan vs IndexScan menggunakan CostModel.
- Mengganti TableScan dengan IndexScan jika lebih murah.
- Mendukung kesamaan (op = EQ) dan rentang (GT, GE, LT, LE).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from common.catalog import IndexInfo, IndexKind
from common.expression import (
    And,
    ColumnRef,
    Comparison,
    CompOp,
    Expression,
    Literal,
)
from common.interfaces import StorageManager
from common.plan import (
    Filter,
    IndexScan,
    Join,
    PlanNode,
    TableScan,
)

if TYPE_CHECKING:
    from query_optimizer.cost_model import CostModel


class AccessPathChooser:
    """Memilih jalur akses terbaik: TableScan atau IndexScan.

    Untuk setiap TableScan dalam tree, cek apakah ada Filter di atasnya
    yang kondisinya cocok dengan index yang tersedia. Jika IndexScan
    lebih murah, lakukan penggantian.

    Pipeline Planner memanggil `choose()` setelah JoinRuleRewriter,
    sebelum JoinOrderOptimizer.
    """

    def __init__(self, sm: StorageManager) -> None:
        self._sm = sm

    def choose(self, plan: PlanNode, cost_model: CostModel) -> PlanNode:
        """Traversal rekursif; ganti TableScan dengan IndexScan jika lebih murah."""
        return self._optimize_node(plan, cost_model)

    # ------------------------------------------------------------------
    # Traversal
    # ------------------------------------------------------------------

    def _optimize_node(self, node: PlanNode, cost_model: CostModel) -> PlanNode:
        """Bottom-up: optimalkan anak dulu, lalu node ini."""
        # Rekursif ke anak
        if node.children:
            new_children: list[PlanNode] = []
            for child in node.children:
                new_children.append(self._optimize_node(child, cost_model))
            node.children = new_children

        # Pola: Filter(cond) → TableScan(table)
        if isinstance(node, Filter) and node.children:
            child = node.children[0]
            if isinstance(child, TableScan):
                return self._try_index_scan(node, child, cost_model)

        return node

    # ------------------------------------------------------------------
    # Coba ganti TableScan dengan IndexScan
    # ------------------------------------------------------------------

    def _try_index_scan(
        self,
        filt: Filter,
        scan: TableScan,
        cost_model: CostModel,
    ) -> PlanNode:
        """Cek apakah ada index yang bisa dipakai; ganti jika lebih murah."""
        try:
            indexes = self._sm.list_indexes(scan.table)
        except Exception:
            return filt

        if not indexes:
            return filt

        # Kumpulkan predikat dari Filter
        predicates = self._flatten_and(filt.condition)

        best_plan: PlanNode = filt
        best_cost: float = cost_model.get_cost(filt)

        for index in indexes:
            for pred in predicates:
                index_scan = self._build_index_scan(scan, index, pred, filt)
                if index_scan is None:
                    continue

                candidate_cost = cost_model.get_cost(index_scan)
                if candidate_cost < best_cost:
                    best_cost = candidate_cost
                    best_plan = index_scan

        return best_plan

    def _build_index_scan(
        self,
        scan: TableScan,
        index: IndexInfo,
        pred: Expression,
        original_filter: Filter,
    ) -> PlanNode | None:
        """Coba bangun IndexScan dari predikat tunggal.

        Mengembalikan None jika predikat tidak cocok dengan index.
        Mengembalikan Filter(IndexScan) jika ada predikat sisa.
        """
        if not isinstance(pred, Comparison):
            return None

        # Pastikan predikat menyebut kolom yang diindeks
        col_ref, literal = self._extract_col_and_literal(pred)
        if col_ref is None or literal is None:
            return None

        # Kolom harus cocok dengan index
        if col_ref.column != index.column:
            return None
        if col_ref.table and col_ref.table != index.table:
            return None

        op: CompOp = pred.op
        key = literal.value

        # Hash index hanya mendukung EQ
        if index.kind == IndexKind.HASH and op != CompOp.EQ:
            return None

        # Bangun IndexScan
        if op == CompOp.EQ:
            index_scan: PlanNode = IndexScan(
                table=scan.table,
                index=index,
                op=op,
                key=key,
                children=[],
                output_columns=scan.output_columns,
            )
        elif op in (CompOp.GT, CompOp.GE):
            index_scan = IndexScan(
                table=scan.table,
                index=index,
                op=op,
                low=key,
                children=[],
                output_columns=scan.output_columns,
            )
        elif op in (CompOp.LT, CompOp.LE):
            index_scan = IndexScan(
                table=scan.table,
                index=index,
                op=op,
                high=key,
                children=[],
                output_columns=scan.output_columns,
            )
        else:
            return None

        # Jika ada predikat sisa selain pred ini, bungkus dengan Filter
        remaining = self._remaining_predicates(original_filter.condition, pred)
        if remaining is not None:
            return Filter(
                condition=remaining,
                children=[index_scan],
                output_columns=original_filter.output_columns,
            )

        return index_scan

    # ------------------------------------------------------------------
    # Helper ekspresi
    # ------------------------------------------------------------------

    def _extract_col_and_literal(
        self, pred: Comparison
    ) -> tuple[ColumnRef | None, Literal | None]:
        """Ekstrak (ColumnRef, Literal) dari predikat. Tangani kedua arah."""
        left, right = pred.left, pred.right

        if isinstance(left, ColumnRef) and isinstance(right, Literal):
            return left, right
        if isinstance(left, Literal) and isinstance(right, ColumnRef):
            # Balik predikat agar kolom selalu di kiri
            return right, left

        return None, None

    def _flatten_and(self, expr: Expression) -> list[Expression]:
        """Pecah And menjadi daftar datar."""
        if isinstance(expr, And):
            result: list[Expression] = []
            for cond in expr.conditions:
                result.extend(self._flatten_and(cond))
            return result
        return [expr]

    def _remaining_predicates(
        self, original: Expression, used: Expression
    ) -> Expression | None:
        """Kembalikan predikat dari `original` kecuali `used`. None jika tidak ada sisa."""
        predicates = self._flatten_and(original)
        remaining = [p for p in predicates if p is not used]

        if not remaining:
            return None
        if len(remaining) == 1:
            return remaining[0]
        return And(conditions=remaining)

