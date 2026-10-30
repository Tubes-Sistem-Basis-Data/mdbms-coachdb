"""QO-15: Menampilkan Query Plan sebelum dan sesudah Optimasi.

PIC: QO1 (Angelina / ketua group)
Status: STUB — menunggu implementasi.
"""

from __future__ import annotations

from common.expression import (
    And,
    BinaryOp,
    ColumnRef,
    Comparison,
    Expression,
    Literal,
)
from common.plan import (
    CreateIndex,
    Delete,
    Filter,
    IndexScan,
    Insert,
    Join,
    PlanNode,
    Project,
    Sort,
    TableScan,
    Update,
)


class PlanPrinter:
    """Menampilkan plan tree sebagai teks indentasi untuk perintah EXPLAIN.

    TODO (QO1): Implementasikan render dan build_explain.
    """

    def render(self, plan: PlanNode, depth: int = 0) -> str:
        """Render plan tree menjadi string teks berindentasi."""
        # STUB: menunggu implementasi QO1
        return type(plan).__name__

    def build_explain(
        self,
        before: PlanNode | None,
        after: PlanNode | None,
        cost_before: float,
        cost_after: float,
    ) -> str:
        """Bangun teks EXPLAIN: plan sebelum, plan sesudah, biaya."""
        # STUB: menunggu implementasi QO1
        lines: list[str] = []
        lines.append("Plan awal:")
        if before is not None:
            lines.append(self.render(before))
        else:
            lines.append("  (tidak ada plan)")
        lines.append("")
        lines.append("Plan optimal:")
        if after is not None:
            lines.append(self.render(after))
        else:
            lines.append("  (tidak ada plan)")
        lines.append("")
        lines.append(f"Biaya awal : {cost_before:g} blok")
        lines.append(f"Biaya akhir: {cost_after:g} blok")
        return "\n".join(lines)

