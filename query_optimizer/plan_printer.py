"""Tampilan query plan untuk EXPLAIN."""

from __future__ import annotations

from typing import Any

from common.plan import PlanNode


class PlanPrinter:
    """Mengubah plan menjadi teks untuk perintah EXPLAIN (QO-15)."""

    def render(self, plan: PlanNode | None) -> str:
        """Ubah satu plan jadi teks pohon."""
        return ""

    def build_explain(self, before: PlanNode | None, after: PlanNode | None) -> str:
        """Gabungkan plan sebelum dan sesudah optimasi beserta biayanya."""
        return ""


def render_plan(plan: Any | None) -> str:
    """Ubah plan jadi teks yang bisa dibaca user (QO-15)."""
    return "mock plan"
