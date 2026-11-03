from common.plan import PlanNode

class SelectionRewriter:
    def apply(self, plan: PlanNode) -> PlanNode:
        """Menjalankan seluruh optimasi seleksi"""
        return plan

    def _split_conjunction(self, plan: PlanNode) -> PlanNode:
        """Memecah kondisi AND menjadi beberapa filter"""
        return plan

    def _reorder(self, plan: PlanNode) -> PlanNode:
        """Mengatur urutan operasi filter"""
        return plan

    def _push_down(self, plan: PlanNode) -> PlanNode:
        """Melakukan penyaringan data lebih awal"""
        return plan