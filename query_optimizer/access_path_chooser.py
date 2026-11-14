"""Pemilihan jalur akses: mengganti TableScan dengan IndexScan jika lebih murah (QO-13)."""

from __future__ import annotations

import copy
from math import isfinite

from common.catalog import ColumnSchema, DataType, IndexInfo, IndexKind, TableSchema
from common.errors import SemanticError
from common.expression import And, ColumnRef, Comparison, CompOp, Expression, Literal
from common.interfaces import StorageManager
from common.plan import Filter, IndexScan, PlanNode, Project, TableScan, Update, Delete
from .cost_model import CostModel


_NUMERIC = (DataType.INTEGER, DataType.FLOAT)
_TEXT = (DataType.CHAR, DataType.VARCHAR)
_FLIP_OP = {
    CompOp.EQ: CompOp.EQ,
    CompOp.NE: CompOp.NE,
    CompOp.LT: CompOp.GT,
    CompOp.LE: CompOp.GE,
    CompOp.GT: CompOp.LT,
    CompOp.GE: CompOp.LE,
}


def _compatible_type(col_type: DataType, val: object) -> bool:
    val_type = {int: DataType.INTEGER, float: DataType.FLOAT, str: DataType.VARCHAR}.get(type(val))
    if val_type is None:
        return False
    if val_type is DataType.FLOAT and not isfinite(val):  # type: ignore[arg-type]
        return False
    return (col_type in _NUMERIC and val_type in _NUMERIC) or (col_type in _TEXT and val_type in _TEXT)


def _flatten_and(expr: Expression) -> list[Expression]:
    if isinstance(expr, And):
        result: list[Expression] = []
        for cond in expr.conditions:
            result.extend(_flatten_and(cond))
        return result
    return [expr]


def _extract_col_op_literal(pred: Expression) -> tuple[ColumnRef, CompOp, Literal] | None:
    if not isinstance(pred, Comparison) or not isinstance(pred.op, CompOp):
        return None
    if isinstance(pred.left, ColumnRef) and isinstance(pred.right, Literal):
        return pred.left, pred.op, pred.right
    if isinstance(pred.left, Literal) and isinstance(pred.right, ColumnRef):
        flipped = _FLIP_OP.get(pred.op)
        if flipped is None:
            return None
        return pred.right, flipped, pred.left
    return None


def _safe_cost(node: PlanNode, cost_model: CostModel) -> float:
    try:
        return cost_model.get_cost(node)
    except SemanticError:
        return float("inf")


def _index_scan_rows(node: PlanNode) -> float:
    if isinstance(node, IndexScan):
        return node.estimated_rows
    for child in node.children:
        rows = _index_scan_rows(child)
        if rows < float("inf"):
            return rows
    return float("inf")


class AccessPathChooser:
    """Mengganti TableScan dengan IndexScan jika lebih murah (QO-13)."""

    def __init__(self, catalog: StorageManager | None = None) -> None:
        self._catalog = catalog

    def choose(self, plan: PlanNode, cost_model: CostModel) -> PlanNode:
        """Telusuri pohon plan dan ganti Filter(TableScan) dengan IndexScan bila biayanya lebih rendah."""
        catalog = self._catalog if self._catalog is not None else getattr(cost_model, "_catalog", None)
        if catalog is None:
            return plan
        return self._optimize_node(copy.deepcopy(plan), cost_model, catalog, parent_is_write=False)

    def _optimize_node(
        self,
        node: PlanNode,
        cost_model: CostModel,
        catalog: StorageManager,
        parent_is_write: bool = False,
    ) -> PlanNode:
        if isinstance(node, Filter):
            chain_result = self._extract_scan_chain(node, allow_project=not parent_is_write)
            if chain_result is not None:
                scan, predicates, top_proj = chain_result
                return self._try_index_scan(node, scan, predicates, top_proj, cost_model, catalog)

        is_write = isinstance(node, (Update, Delete))
        if node.children:
            node.children = [
                self._optimize_node(child, cost_model, catalog, parent_is_write=is_write)
                for child in node.children
            ]

        if isinstance(node, Project) and len(node.children) == 1 and isinstance(node.children[0], Project):
            inner_proj = node.children[0]
            if not inner_proj.aliases:
                node.children = inner_proj.children

        return node

    def _extract_scan_chain(
        self, top_filter: Filter, allow_project: bool = True,
    ) -> tuple[TableScan, list[Expression], Project | None] | None:
        predicates: list[Expression] = []
        top_proj: Project | None = None
        curr: PlanNode = top_filter

        while True:
            if isinstance(curr, Filter) and len(curr.children) == 1:
                predicates.extend(_flatten_and(curr.condition))
                curr = curr.children[0]
            elif allow_project and isinstance(curr, Project) and len(curr.children) == 1 and not curr.aliases:
                if top_proj is None:
                    top_proj = curr
                curr = curr.children[0]
            else:
                break

        if isinstance(curr, TableScan):
            return curr, predicates, top_proj
        return None

    def _try_index_scan(
        self,
        original_node: Filter,
        scan: TableScan,
        predicates: list[Expression],
        top_proj: Project | None,
        cost_model: CostModel,
        catalog: StorageManager,
    ) -> PlanNode:
        try:
            schema = catalog.get_schema(scan.table)
        except Exception:
            return original_node

        if not schema.indexes or not predicates:
            return original_node

        best_plan: PlanNode = original_node
        best_cost = _safe_cost(original_node, cost_model)
        best_idx_rows = float("inf")

        for index in schema.indexes:
            if index.table != scan.table:
                continue
            col_schema = self._find_column(schema, index.column)
            if col_schema is None:
                continue

            for candidate in self._candidates_for_index(
                scan, index, col_schema, predicates, top_proj, original_node,
            ):
                cand_cost = _safe_cost(candidate, cost_model)
                cand_idx_rows = _index_scan_rows(candidate)
                if cand_cost < best_cost - 1e-9 or (
                    best_plan is not original_node
                    and abs(cand_cost - best_cost) <= 1e-9
                    and cand_idx_rows < best_idx_rows - 1e-9
                ):
                    best_cost = cand_cost
                    best_idx_rows = cand_idx_rows
                    best_plan = candidate

        return best_plan

    def _candidates_for_index(
        self,
        scan: TableScan,
        index: IndexInfo,
        col_schema: ColumnSchema,
        predicates: list[Expression],
        top_proj: Project | None,
        original_node: Filter,
    ) -> list[PlanNode]:
        candidates: list[PlanNode] = []
        lower_preds: list[tuple[int, CompOp, object]] = []
        upper_preds: list[tuple[int, CompOp, object]] = []

        for idx, pred in enumerate(predicates):
            matched = self._match_predicate(pred, scan, index, col_schema)
            if matched is None:
                continue
            op, val = matched
            if op is CompOp.EQ:
                idx_scan = IndexScan(
                    table=scan.table,
                    index=index,
                    op=CompOp.EQ,
                    key=val,
                    alias=scan.alias,
                    output_columns=list(scan.output_columns),
                )
                candidates.append(
                    self._wrap_remaining(idx_scan, predicates, {idx}, top_proj, original_node)
                )
            elif op in (CompOp.GT, CompOp.GE):
                lower_preds.append((idx, op, val))
                idx_scan = IndexScan(
                    table=scan.table,
                    index=index,
                    op=op,
                    low=val,
                    alias=scan.alias,
                    output_columns=list(scan.output_columns),
                )
                candidates.append(
                    self._wrap_remaining(idx_scan, predicates, {idx}, top_proj, original_node)
                )
            elif op in (CompOp.LT, CompOp.LE):
                upper_preds.append((idx, op, val))
                idx_scan = IndexScan(
                    table=scan.table,
                    index=index,
                    op=op,
                    high=val,
                    alias=scan.alias,
                    output_columns=list(scan.output_columns),
                )
                candidates.append(
                    self._wrap_remaining(idx_scan, predicates, {idx}, top_proj, original_node)
                )

        if index.kind is IndexKind.BPLUS_TREE and lower_preds and upper_preds:
            for low_idx, low_op, low_val in lower_preds:
                for high_idx, high_op, high_val in upper_preds:
                    if low_idx == high_idx:
                        continue
                    try:
                        if low_val > high_val:  # type: ignore[operator]
                            continue
                    except TypeError:
                        continue
                    idx_scan = IndexScan(
                        table=scan.table,
                        index=index,
                        op=low_op,
                        low=low_val,
                        high=high_val,
                        alias=scan.alias,
                        output_columns=list(scan.output_columns),
                    )
                    consumed = {low_idx, high_idx} if high_op is CompOp.LE else {low_idx}
                    candidates.append(
                        self._wrap_remaining(idx_scan, predicates, consumed, top_proj, original_node)
                    )

        return candidates

    def _match_predicate(
        self,
        pred: Expression,
        scan: TableScan,
        index: IndexInfo,
        col_schema: ColumnSchema,
    ) -> tuple[CompOp, object] | None:
        extracted = _extract_col_op_literal(pred)
        if extracted is None:
            return None
        col_ref, op, literal = extracted
        if op not in (CompOp.EQ, CompOp.GT, CompOp.GE, CompOp.LT, CompOp.LE):
            return None
        if index.kind is IndexKind.HASH and op is not CompOp.EQ:
            return None
        if col_ref.column != index.column:
            return None
        allowed_tables = {scan.table}
        if scan.alias is not None:
            allowed_tables.add(scan.alias)
        if col_ref.table is not None and col_ref.table not in allowed_tables:
            return None
        val = literal.value
        if val is None or not _compatible_type(col_schema.type, val):
            return None
        return op, val

    def _wrap_remaining(
        self,
        idx_scan: IndexScan,
        predicates: list[Expression],
        consumed_indices: set[int],
        top_proj: Project | None,
        original_node: Filter,
    ) -> PlanNode:
        remaining = [pred for i, pred in enumerate(predicates) if i not in consumed_indices]
        result: PlanNode = idx_scan
        if remaining:
            cond = remaining[0] if len(remaining) == 1 else And(conditions=remaining)
            out_cols = list(idx_scan.output_columns) if top_proj is not None else list(original_node.output_columns)
            result = Filter(condition=cond, children=[idx_scan], output_columns=out_cols)
        if top_proj is not None and top_proj.columns:
            result = Project(
                columns=list(top_proj.columns),
                aliases=list(top_proj.aliases),
                children=[result],
                output_columns=list(top_proj.output_columns),
            )
        return result

    @staticmethod
    def _find_column(schema: TableSchema, name: str) -> ColumnSchema | None:
        for col in schema.columns:
            if col.name == name:
                return col
        return None
