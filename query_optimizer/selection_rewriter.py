"""QO-09: Mengoptimasi Seleksi.

PIC: QO4 (Faiz)
Status: STUB — menunggu implementasi.
"""

from __future__ import annotations

from common.plan import PlanNode


class SelectionRewriter:
    """Optimasi heuristik pada seleksi (Filter node).

    Tugas:
    - split_conjunction: Pecah kondisi AND menjadi Filter bertingkat.
    - reorder: Urutkan Filter paling selektif di bawah.
    - push_down: Dorong Filter sedekat mungkin ke daun (sumber data).

    TODO (QO4): Implementasikan ketiga transformasi di atas.
    """

    def apply(self, plan: PlanNode) -> PlanNode:
        """Jalankan semua optimasi seleksi secara berurutan."""
        plan = self._split_conjunction(plan)
        plan = self._reorder(plan)
        plan = self._push_down(plan)
        return plan

    def _split_conjunction(self, plan: PlanNode) -> PlanNode:
        """Pecah Filter(A AND B) menjadi Filter(A) -> Filter(B)."""
        # STUB: menunggu implementasi QO4
        return plan

    def _reorder(self, plan: PlanNode) -> PlanNode:
        """Urutkan Filter bertingkat: yang paling selektif di bawah."""
        # STUB: menunggu implementasi QO4
        return plan

    def _push_down(self, plan: PlanNode) -> PlanNode:
        """Dorong Filter melewati Project dan Join ke dekat sumber data."""
        # STUB: menunggu implementasi QO4
        return plan

