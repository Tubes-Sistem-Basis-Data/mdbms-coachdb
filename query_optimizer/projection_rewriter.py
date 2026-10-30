"""QO-10: Mengoptimasi Proyeksi.

PIC: QO4 (Faiz)
Status: STUB — menunggu implementasi.
"""

from __future__ import annotations

from common.plan import PlanNode


class ProjectionRewriter:
    """Optimasi heuristik pada proyeksi (Project node).

    Tugas:
    - merge_projects: Gabung Project bertingkat menjadi satu Project.
    - push_down: Sisipkan Project di bawah Join untuk memperkecil kolom yang diproses.

    TODO (QO4): Implementasikan kedua transformasi di atas.
    """

    def apply(self, plan: PlanNode) -> PlanNode:
        """Jalankan semua optimasi proyeksi secara berurutan."""
        plan = self._merge_projects(plan)
        plan = self._push_down(plan)
        return plan

    def _merge_projects(self, plan: PlanNode) -> PlanNode:
        """Gabung Project(Project(child)) menjadi satu Project(child)."""
        # STUB: menunggu implementasi QO4
        return plan

    def _push_down(self, plan: PlanNode) -> PlanNode:
        """Sisipkan Project di bawah Join agar kolom yang tidak dipakai dibuang lebih awal."""
        # STUB: menunggu implementasi QO4
        return plan

