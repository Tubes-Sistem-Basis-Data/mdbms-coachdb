"""QO-14: Memilih Query Plan Terbaik (Planner / Koordinator Optimasi).

PIC: QO5 (Dzakwan)
Tanggung jawab:
- Mengkoordinasikan seluruh pipeline optimasi (Zona 3).
- Menjalankan rewriter heuristik secara berurutan.
- Menggunakan AccessPathChooser dan JoinOrderOptimizer.
- Menghasilkan kandidat plan, menghitung biaya, memilih yang termurah.
- Mengisi ParsedQuery.plan dan ParsedQuery.estimated_cost.
"""

from __future__ import annotations

import copy

from common.plan import ParsedQuery, PlanNode

from query_optimizer.access_path_chooser import AccessPathChooser
from query_optimizer.cost_model import CostModel
from query_optimizer.join_order_optimizer import JoinOrderOptimizer
from query_optimizer.join_rule_rewriter import JoinRuleRewriter
from query_optimizer.projection_rewriter import ProjectionRewriter
from query_optimizer.selection_rewriter import SelectionRewriter


class Planner:
    """Koordinator pipeline optimasi query.

    Urutan transformasi:
    1. SelectionRewriter  — split conjunction, reorder, push-down seleksi
    2. ProjectionRewriter — merge project, push-down proyeksi
    3. JoinRuleRewriter   — ubah Cartesian → Theta Join (QO-11)
    4. AccessPathChooser  — pilih TableScan atau IndexScan (QO-13)
    5. JoinOrderOptimizer — urutan join terbaik (QO-12)
    6. generate_candidates & pick_cheapest → plan final (QO-14)

    Catatan: Untuk query bukan SELECT (INSERT/UPDATE/DELETE/CREATE INDEX/
    TRANSACTION), plan dikembalikan apa adanya (tidak perlu dioptimasi).
    """

    def __init__(
        self,
        cost_model: CostModel,
        sel_rewriter: SelectionRewriter,
        proj_rewriter: ProjectionRewriter,
        join_rewriter: JoinRuleRewriter,
        access_chooser: AccessPathChooser,
        join_order_opt: JoinOrderOptimizer,
    ) -> None:
        self._cost_model = cost_model
        self._sel_rewriter = sel_rewriter
        self._proj_rewriter = proj_rewriter
        self._join_rewriter = join_rewriter
        self._access_chooser = access_chooser
        self._join_order_opt = join_order_opt

    def optimize(self, query: ParsedQuery) -> ParsedQuery:
        """Optimalkan query; kembalikan ParsedQuery dengan plan termurah.

        Jika query tidak punya plan (transaksi, CREATE INDEX tanpa perlu
        optimasi), kembalikan apa adanya.
        """
        if query.plan is None:
            return query

        # Simpan plan awal untuk perbandingan
        original_plan = query.plan

        # --- Pipeline heuristik ---
        plan = self._sel_rewriter.apply(original_plan)
        plan = self._proj_rewriter.apply(plan)
        plan = self._join_rewriter.apply(plan)

        # --- Pilih jalur akses ---
        plan = self._access_chooser.choose(plan, self._cost_model)

        # --- Reorder join ---
        plan = self._join_order_opt.reorder(plan, self._cost_model)

        # --- Enumerasi kandidat dan pilih termurah ---
        candidates = self._generate_candidates(plan)
        best_plan = self._pick_cheapest(candidates)

        if best_plan is not None:
            query.plan = best_plan
            query.estimated_cost = self._cost_model.get_cost(best_plan)
        else:
            query.plan = plan
            query.estimated_cost = self._cost_model.get_cost(plan)

        return query

    def _generate_candidates(self, plan: PlanNode) -> list[PlanNode]:
        """Hasilkan daftar kandidat plan dari variasi transformasi.

        Kandidat saat ini:
        - plan apa adanya setelah rewriter
        - plan dengan sisi join di-swap (commutativity)

        TODO (QO5): tambahkan eksplorasi lebih lanjut jika diperlukan.
        """
        candidates: list[PlanNode] = [plan]

        # Tambahkan variasi swap-sisi join (commutativity)
        swapped = self._swap_join_sides(plan)
        if swapped is not plan:
            candidates.append(swapped)

        return candidates

    def _pick_cheapest(self, candidates: list[PlanNode]) -> PlanNode | None:
        """Pilih plan dengan estimasi biaya terendah.

        Aturan: jika biaya sama, pilih kandidat pertama (stabil).
        """
        if not candidates:
            return None

        best = candidates[0]
        best_cost = self._cost_model.get_cost(best)

        for candidate in candidates[1:]:
            cost = self._cost_model.get_cost(candidate)
            if cost < best_cost:
                best_cost = cost
                best = candidate

        return best

    # ------------------------------------------------------------------
    # Variasi plan: swap sisi join
    # ------------------------------------------------------------------

    def _swap_join_sides(self, node: PlanNode) -> PlanNode:
        """Buat salinan plan dengan sisi kiri-kanan Join di-swap (commutativity)."""
        from common.plan import Join

        if isinstance(node, Join) and len(node.children) == 2:
            left, right = node.children
            swapped_left = self._swap_join_sides(right)
            swapped_right = self._swap_join_sides(left)
            return Join(
                kind=node.kind,
                condition=node.condition,
                algorithm=node.algorithm,
                children=[swapped_left, swapped_right],
                output_columns=node.output_columns,
                estimated_rows=node.estimated_rows,
                estimated_cost=node.estimated_cost,
            )

        if node.children:
            new_children = [self._swap_join_sides(c) for c in node.children]
            node.children = new_children

        return node

