"""Tampilan query plan untuk EXPLAIN."""

from __future__ import annotations

from typing import Any

from common.plan import PlanNode

# TODO(QO-15): import semua node plan (TableScan, IndexScan, Filter, Project, Join, Sort,
# Insert, Update, Delete, CreateIndex) dan node ekspresi (ColumnRef, Literal, BinaryOp,
# Comparison, And) untuk dipakai di match-case.


class PlanPrinter:
    """Mengubah plan menjadi teks untuk perintah EXPLAIN (QO-15)."""

    def render(self, plan: PlanNode | None) -> str:
        """Ubah satu plan jadi teks pohon."""
        # TODO(QO-15):
        # - plan None -> "(tidak ada plan)"
        # - selain itu "\n".join(self._render_node(plan, 0))
        # Buat helper _render_node(node, depth) -> list[str]:
        #   baris = "  " * depth + self._label(node), lalu tambahkan hasil
        #   _render_node(anak, depth + 1) untuk setiap node.children (urut, deterministik).
        # Buat helper _label(node) dengan match-case per kelas, contoh:
        #   TableScan  -> "TableScan [student]"
        #   IndexScan  -> "IndexScan [student.idx_id: id = 7]"
        #   Filter     -> "Filter [student.gpa > 3.5]"
        #   Project    -> "Project [student.name, student.id]" (columns kosong -> "Project [*]")
        #   Join       -> "Join BNLJ THETA [a.id = b.a_id]" (CROSS/NATURAL tanpa kondisi)
        #   Sort       -> "Sort [student.gpa DESC]"
        #   Insert/Update/Delete/CreateIndex -> nama node + tabel/kolom penting
        # Opsional untuk demo: parameter show_estimates yang menambah
        # "(n=..., biaya=...)" dari estimated_rows dan estimated_cost.
        return ""

    # TODO(QO-15): tambahkan helper _render_expr(expr) -> str
    #   ColumnRef  -> "tabel.kolom", atau "kolom" kalau table None
    #   Literal    -> str pakai kutip tunggal, None jadi NULL, angka apa adanya
    #   BinaryOp   -> "(kiri op kanan)" memakai op.value
    #   Comparison -> "kiri op kanan" memakai op.value
    #   And        -> " AND ".join(...) untuk setiap kondisi

    def build_explain(self, before: PlanNode | None, after: PlanNode | None) -> str:
        """Gabungkan plan sebelum dan sesudah optimasi beserta biayanya."""
        # TODO(QO-15): tambahkan parameter cost_before: float dan cost_after: float.
        # Format yang disarankan:
        #   SEBELUM (biaya 130 blok)
        #   <render(before)>
        #   <baris kosong>
        #   SESUDAH (biaya 22 blok)
        #   <render(after)>
        # Pakai f"{biaya:g}" supaya 130.0 tampil sebagai 130.
        return ""


def render_plan(plan: Any | None) -> str:
    """Ubah plan jadi teks yang bisa dibaca user (QO-15)."""
    # TODO(QO-15): cukup return PlanPrinter().render(plan)
    return "mock plan"
