"""Penyusun plan: rewrite join, pilih jalur akses, atur urutan join, dan pilih plan terbaik (QO-14)."""

from __future__ import annotations

import copy

from common.errors import SemanticError
from common.expression import And, ColumnRef, Expression
from common.plan import (
    CreateIndex,
    CreateTable,
    Delete,
    DropTable,
    Filter,
    IndexScan,
    Insert,
    Join,
    Limit,
    ParsedQuery,
    PlanNode,
    Project,
    Sort,
    SubqueryScan,
    TableScan,
    Update,
)

from .access_path_chooser import AccessPathChooser
from .cost_model import CostModel
from .genetic_join_optimizer import GeneticJoinOptimizer
from .join_order_optimizer import JoinOrderOptimizer
from .join_rule_rewriter import JoinRuleRewriter
from .projection_rewriter import ProjectionRewriter
from .selection_rewriter import SelectionRewriter

_NON_ROW_ROOTS = (Insert, CreateIndex, CreateTable, DropTable)


class Planner:
    """Menjalankan rewrite dan pemilihan plan termurah (QO-11 sampai QO-14)."""

    def __init__(
        self,
        cost_model: CostModel,
        join_search: str = "heuristic",
        genetic: GeneticJoinOptimizer | None = None,
    ) -> None:
        """`join_search` "heuristic" memakai JoinOrderOptimizer, "ga" memakai GeneticJoinOptimizer."""
        if join_search not in ("heuristic", "ga"):
            raise ValueError("join_search harus 'heuristic' atau 'ga'")
        self._cost_model = cost_model
        self._join_search = join_search
        self._access_chooser = AccessPathChooser()
        self._join_order_opt = JoinOrderOptimizer()
        self._genetic_opt = genetic if genetic is not None else GeneticJoinOptimizer()
        self._join_rule_rewriter = JoinRuleRewriter()
        self._selection_rewriter = SelectionRewriter()
        self._projection_rewriter = ProjectionRewriter()

    def optimize(self, query: ParsedQuery) -> ParsedQuery:
        """Optimasi plan di query dan pilih plan dengan estimasi biaya terkecil."""
        if query.plan is None:
            return query

        plan = query.plan
        if isinstance(plan, _NON_ROW_ROOTS):
            query.estimated_cost = 0.0
            return query

        if isinstance(plan, (Update, Delete)):
            updated_plan = copy.deepcopy(plan)
            optimized_children: list[PlanNode] = []
            total_cost = 0.0
            for child in updated_plan.children:
                best_child = self._optimize_dml_child(child)
                optimized_children.append(best_child)
                try:
                    total_cost += self._cost_model.get_cost(best_child)
                except SemanticError:
                    pass
            updated_plan.children = optimized_children
            query.plan = updated_plan
            query.estimated_cost = total_cost
            return query

        candidates = self._generate_candidates(plan)
        best_plan = self._pick_cheapest(candidates)
        query.plan = best_plan
        try:
            query.estimated_cost = self._cost_model.get_cost(best_plan)
        except SemanticError:
            pass
        return query

    def _optimize_dml_child(self, child: PlanNode) -> PlanNode:
        """Optimasi sumber baris pada UPDATE/DELETE tanpa menyisipkan Project."""
        candidates: list[PlanNode] = [copy.deepcopy(child)]

        try:
            sel_plan = self._selection_rewriter.apply(copy.deepcopy(child))
            candidates.append(sel_plan)
        except Exception:
            sel_plan = copy.deepcopy(child)

        for base in (copy.deepcopy(child), sel_plan):
            try:
                idx_plan = self._access_chooser.choose(copy.deepcopy(base), self._cost_model)
                candidates.append(idx_plan)
            except Exception:
                pass

        return self._pick_cheapest(candidates)

    def _generate_candidates(self, plan: PlanNode) -> list[PlanNode]:
        """Bangkitkan berbagai kandidat plan ekivalen untuk dievaluasi biayanya."""
        baseline = copy.deepcopy(plan)
        candidates: list[PlanNode] = [baseline]

        # Kupas Project perantara hasil pushdown lebih awal supaya
        # multi-level JoinRule, Selection pushdown, IndexScan, dan JoinOrder
        # tidak terhalang oleh Project di antara Filter dan Join/TableScan.
        unprojected = self._strip_pushed_projects(copy.deepcopy(baseline))
        if repr(unprojected) != repr(baseline):
            candidates.extend(self._pipeline_variants(unprojected))
            candidates.append(copy.deepcopy(unprojected))

        # Optimasi langsung pada pohon plan masukan
        candidates.extend(self._pipeline_variants(baseline))

        return self._deduplicate(candidates)

    def _pipeline_variants(self, seed: PlanNode) -> list[PlanNode]:
        """Hasilkan variasi plan dari tahapan rewrite aturan, akses indeks, dan urutan join."""
        variants: list[PlanNode] = []

        # Tahap A: JoinRule + Selection + JoinRule berulang untuk multi-join.
        # Gabungkan juga rantai Filter sebelum JoinRule supaya kondisi antar-tabel
        # yang sudah terpecah oleh SelectionRewriter tetap bisa diubah jadi Theta Join.
        stage_rule = copy.deepcopy(seed)
        try:
            for _ in range(3):
                prev_repr = repr(stage_rule)
                stage_rule = self._combine_chained_filters(stage_rule)
                stage_rule = self._push_filter_into_cross_child(stage_rule)
                stage_rule = self._join_rule_rewriter.apply(stage_rule)
                stage_rule = self._selection_rewriter.apply(stage_rule)
                stage_rule = self._combine_chained_filters(stage_rule)
                stage_rule = self._push_filter_into_cross_child(stage_rule)
                stage_rule = self._join_rule_rewriter.apply(stage_rule)
                stage_rule = self._selection_rewriter.apply(stage_rule)
                if repr(stage_rule) == prev_repr:
                    break
        except Exception:
            stage_rule = copy.deepcopy(seed)

        # Prioritaskan basis yang sudah menerapkan JoinRule (Theta Join) sebelum basis mentah
        access_bases: list[PlanNode] = []
        if repr(stage_rule) != repr(seed):
            access_bases.append(copy.deepcopy(stage_rule))
        access_bases.append(copy.deepcopy(seed))

        # Tahap B: Pemilihan jalur akses (IndexScan vs TableScan)
        access_plans: list[PlanNode] = []
        for base in access_bases:
            try:
                chosen = self._access_chooser.choose(copy.deepcopy(base), self._cost_model)
                access_plans.append(chosen)
            except Exception:
                pass

        # Tahap C: Optimasi urutan join (sebelum dan sesudah pemilihan IndexScan)
        join_bases = access_plans + list(access_bases)
        reordered_plans: list[PlanNode] = []
        for base in join_bases:
            try:
                reordered_then_idx = self._access_chooser.choose(
                    self._reorder_joins(copy.deepcopy(base)),
                    self._cost_model,
                )
                reordered_plans.append(reordered_then_idx)
            except Exception:
                pass

            try:
                reordered = self._reorder_joins(copy.deepcopy(base))
                reordered_plans.append(reordered)
            except Exception:
                pass

        # Tahap D: Terapkan Projection pushdown pada kandidat yang sudah dioptimasi
        projection_bases = reordered_plans + access_plans
        if repr(stage_rule) != repr(seed):
            projection_bases.append(stage_rule)

        projected_plans: list[PlanNode] = []
        for base in projection_bases:
            try:
                projected = self._projection_rewriter.apply(copy.deepcopy(base))
                projected_plans.append(projected)
            except Exception:
                pass

        variants.extend(projected_plans)
        variants.extend(reordered_plans)
        variants.extend(access_plans)
        if repr(stage_rule) != repr(seed):
            variants.append(copy.deepcopy(stage_rule))

        return variants

    def _reorder_joins(self, plan: PlanNode) -> PlanNode:
        """Jalankan pengurutan join sesuai strategi pencarian (heuristic atau ga)."""
        if self._join_search == "ga":
            return self._genetic_opt.search(plan, self._cost_model)
        return self._join_order_opt.reorder(plan, self._cost_model)

    def _combine_chained_filters(self, node: PlanNode) -> PlanNode:
        """Gabungkan rantai Filter berurutan menjadi satu Filter(And(...)) agar JoinRuleRewriter dapat melihat seluruh kondisi."""
        node.children = [self._combine_chained_filters(child) for child in node.children]
        if isinstance(node, Filter) and len(node.children) == 1 and isinstance(node.children[0], Filter):
            conditions: list[Expression] = []
            curr: PlanNode = node
            while isinstance(curr, Filter) and len(curr.children) == 1:
                if isinstance(curr.condition, And):
                    conditions.extend(curr.condition.conditions)
                else:
                    conditions.append(curr.condition)
                curr = curr.children[0]
            merged_cond: Expression = conditions[0] if len(conditions) == 1 else And(conditions=conditions)
            return Filter(
                condition=merged_cond,
                children=[curr],
                output_columns=list(node.output_columns or curr.output_columns),
            )
        return node

    def _push_filter_into_cross_child(self, node: PlanNode) -> PlanNode:
        """Dorong Filter melewati Project jika anaknya adalah Join agar JoinRuleRewriter bisa mengubah Cross Join."""
        node.children = [self._push_filter_into_cross_child(child) for child in node.children]
        if isinstance(node, Filter) and len(node.children) == 1 and isinstance(node.children[0], Project):
            proj = node.children[0]
            if len(proj.children) == 1 and isinstance(proj.children[0], Join):
                inner_join = proj.children[0]
                pushed_filter = Filter(
                    condition=node.condition,
                    children=[inner_join],
                    output_columns=list(inner_join.output_columns),
                )
                proj.children = [pushed_filter]
                return proj
        return node

    def _strip_pushed_projects(self, plan: PlanNode) -> PlanNode:
        """Hapus node Project perantara di bawah root sambil menjaga skema kolom dasar."""
        node = copy.deepcopy(plan)
        stripped = self._strip_node(node, is_root=True)
        self._refresh_tree_outputs(stripped)
        return stripped

    def _strip_node(self, node: PlanNode, is_root: bool) -> PlanNode:
        if isinstance(node, Project) and len(node.children) == 1:
            if is_root:
                node.children = [self._strip_node(node.children[0], is_root=False)]
                return node
            return self._strip_node(node.children[0], is_root=False)

        if isinstance(node, (Sort, Limit)) and len(node.children) == 1:
            node.children = [self._strip_node(node.children[0], is_root=is_root)]
            return node

        node.children = [self._strip_node(child, is_root=False) for child in node.children]
        return node

    def _refresh_tree_outputs(self, node: PlanNode) -> None:
        """Sinkronkan output_columns seluruh subtree secara bottom-up."""
        for child in node.children:
            self._refresh_tree_outputs(child)
        self._refresh_output_columns(node)

    def _refresh_output_columns(self, node: PlanNode) -> None:
        """Sinkronkan output_columns setelah Project perantara dilepas."""
        if isinstance(node, (TableScan, IndexScan, SubqueryScan)):
            if self._cost_model._catalog is not None and isinstance(node, (TableScan, IndexScan)):
                try:
                    schema = self._cost_model._catalog.get_schema(node.table)
                    qualifier = node.alias or node.table
                    node.output_columns = [ColumnRef(qualifier, col.name) for col in schema.columns]
                except Exception:
                    pass
            return

        if isinstance(node, Project):
            if node.columns:
                node.output_columns = list(node.columns)
            elif node.children:
                node.output_columns = list(node.children[0].output_columns)
            return

        if isinstance(node, (Filter, Sort, Limit)) and node.children:
            node.output_columns = list(node.children[0].output_columns)
            return

        if isinstance(node, Join) and len(node.children) == 2:
            left_cols = list(node.children[0].output_columns)
            right_cols = list(node.children[1].output_columns)
            if left_cols or right_cols:
                combined: list[ColumnRef] = list(left_cols)
                for col in right_cols:
                    if col not in combined:
                        combined.append(col)
                node.output_columns = combined

    def _deduplicate(self, candidates: list[PlanNode]) -> list[PlanNode]:
        unique: list[PlanNode] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = repr(candidate)
            if key not in seen:
                seen.add(key)
                unique.append(candidate)
        return unique

    def _pick_cheapest(self, candidates: list[PlanNode]) -> PlanNode:
        """Pilih kandidat plan valid dengan estimasi biaya terkecil."""
        if not candidates:
            return PlanNode()

        best_plan: PlanNode | None = None
        best_cost = float("inf")

        for candidate in candidates:
            scored_candidate = copy.deepcopy(candidate)
            try:
                cost = self._cost_model.get_cost(scored_candidate)
            except SemanticError:
                continue

            if best_plan is None or cost < best_cost - 1e-9:
                best_cost = cost
                best_plan = scored_candidate

        if best_plan is not None:
            return best_plan
        return copy.deepcopy(candidates[0])
