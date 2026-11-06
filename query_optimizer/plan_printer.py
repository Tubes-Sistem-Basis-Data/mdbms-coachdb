"""Tampilan query plan untuk EXPLAIN."""

from __future__ import annotations

from typing import Any

from common.plan import PlanNode

from common.plan import TableScan, IndexScan, Filter, Project, Join, Sort, Limit, SubqueryScan, Insert, Update, Delete, CreateIndex, CreateTable, DropTable
from common.expression import Expression, ColumnRef, Literal, BinaryOp, Comparison, And, Subquery

class PlanPrinter:
    """Mengubah pohon PlanNode jadi teks berindentasi. Dipakai QueryOptimizer.explain."""

    def render(self, plan: PlanNode | None) -> str:
        """Render satu plan, satu node per baris, anak diindentasi dua spasi."""
        if plan is None:
            return "(tidak ada plan)"
        return "\n".join(self._render_node(plan, 0))

    def _render_node(self, node: PlanNode, depth: int) -> list[str]:
        hasil = ["  " * depth + self._label(node)]
        for anak in node.children:
            hasil.extend(self._render_node(anak, depth + 1))
        return hasil

    def _label(self, node: PlanNode) -> str:
        match node:
            case TableScan():
                if node.alias is None:
                    return f"TableScan [{node.table}]"
                return f"TableScan [{node.table} AS {node.alias}]"
            case IndexScan():
                # low/high inklusif, mengikuti SM.index_range (low <= nilai <= high)
                kolom = node.index.column
                awal = f"IndexScan [{node.table}.{node.index.name}: "
                if node.key is not None:
                    return awal + f"{kolom} {node.op.value} {node.key!r}]"
                bagian = []
                if node.low is not None:
                    bagian.append(f"{node.low!r} <=")
                bagian.append(kolom)
                if node.high is not None:
                    bagian.append(f"<= {node.high!r}")
                return awal + " ".join(bagian) + "]"
            case Filter():
                return f"Filter [{self._render_expr(node.condition)}]"
            case Project():
                if not node.columns:
                    return "Project [*]"
                bagian = []
                for i in range(len(node.columns)):
                    teks = self._render_expr(node.columns[i])
                    if node.aliases and node.aliases[i] is not None:
                        teks += f" AS {node.aliases[i]}"
                    bagian.append(teks)
                return "Project [" + ", ".join(bagian) + "]"
            case Join():
                awal = f"Join {node.algorithm} {node.kind.value}"
                if node.condition is None:
                    return awal
                return awal + f" [{self._render_expr(node.condition)}]"
            case Sort():
                arah = "ASC" if node.ascending else "DESC"
                return f"Sort [{self._render_expr(node.column)} {arah}]"
            case Limit():
                return f"Limit [{node.count}]"
            case SubqueryScan():
                return f"SubqueryScan [{node.alias}]"
            case Insert():
                nilai = ", ".join(self._render_expr(v) for v in node.values)
                return f"Insert [{node.table}: ({nilai})]"
            case Update():
                return f"Update [{node.table}: {node.column} = {self._render_expr(node.expression)}]"
            case Delete():
                return f"Delete [{node.table}]"
            case CreateIndex():
                idx = node.index
                return f"CreateIndex [{idx.name} ON {idx.table}({idx.column}) USING {idx.kind.value}]"
            case CreateTable():
                return f"CreateTable [{node.schema.name}]"
            case DropTable():
                return f"DropTable [{node.table}]"
            case _:
                return type(node).__name__

    def _render_expr(self, expr: Expression) -> str:
        match expr:
            case ColumnRef():
                if expr.table is None:
                    return expr.column
                return f"{expr.table}.{expr.column}"
            case Literal():
                if expr.value is None:
                    return "NULL"
                if isinstance(expr.value, str):
                    return f"'{expr.value}'"
                return str(expr.value)
            case BinaryOp():
                return f"({self._render_expr(expr.left)} {expr.op.value} {self._render_expr(expr.right)})"
            case Comparison():
                return f"{self._render_expr(expr.left)} {expr.op.value} {self._render_expr(expr.right)}"
            case And():
                return " AND ".join(self._render_expr(c) for c in expr.conditions)
            case Subquery():
                return "(subquery)"
            case _:
                return type(expr).__name__

    def build_explain(
        self,
        before: PlanNode | None,
        after: PlanNode | None,
        cost_before: float,
        cost_after: float,
    ) -> str:
        """Gabungkan plan sebelum dan sesudah optimasi beserta biayanya (dalam blok)."""
        return (
            f"SEBELUM (biaya {cost_before:g} blok)\n"
            f"{self.render(before)}\n"
            "\n"
            f"SESUDAH (biaya {cost_after:g} blok)\n"
            f"{self.render(after)}"
        )


def render_plan(plan: Any | None) -> str:
    """Ubah plan jadi teks yang bisa dibaca user (QO-15)."""
    return PlanPrinter().render(plan)
