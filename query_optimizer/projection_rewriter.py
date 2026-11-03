from common.plan import PlanNode

class ProjectionRewriter:
    def apply(self, plan: PlanNode) -> PlanNode:
        """Menjalankan seluruh optimasi proyeksi"""
        return plan

    def _merge_projects(self, plan: PlanNode) -> PlanNode:
        """Menggabungkan operasi pemilihan kolom berurutan"""
        return plan

    def _push_down(self, plan: PlanNode) -> PlanNode:
        """Meneruskan hanya kolom yang masih dibutuhkan"""
        return plan