"""Penukaran urutan dan pengelompokan join (QO-12).

M2: Join(A, B) <-> Join(B, A)
M3: Join(Join(A,B), C) <-> Join(A, Join(B,C))
"""

from __future__ import annotations

from common.errors import SemanticError
from common.expression import And, BinaryOp, ColumnRef, Comparison, Expression
from common.plan import IndexScan, Join, JoinKind, PlanNode, Project, SubqueryScan, TableScan
from .cost_model import CostModel
from .plan_utils import source_list


def _is_join(node: PlanNode) -> bool:
    return isinstance(node, Join) and node.kind in (JoinKind.THETA, JoinKind.CROSS)


def _flatten_conditions(expr: Expression | None) -> list[Expression]:
    if expr is None:
        return []
    if isinstance(expr, And):
        result: list[Expression] = []
        for cond in expr.conditions:
            result.extend(_flatten_conditions(cond))
        return result
    return [expr]


def _make_condition(conds: list[Expression]) -> Expression | None:
    if not conds:
        return None
    return conds[0] if len(conds) == 1 else And(conditions=list(conds))


def _expr_tables(expr: Expression) -> set[str] | None:
    if isinstance(expr, ColumnRef):
        return {expr.table} if expr.table else None
    if isinstance(expr, And):
        parts = [_expr_tables(cond) for cond in expr.conditions]
    elif isinstance(expr, (Comparison, BinaryOp)):
        parts = [_expr_tables(expr.left), _expr_tables(expr.right)]
    else:
        return set()
    if any(part is None for part in parts):
        return None
    return set().union(*parts)


def _source_tables(node: PlanNode) -> set[str]:
    if node.output_columns:
        tables = {ref.table for ref in node.output_columns if ref.table}
        if tables:
            return tables
    return set(_leaf_tables(node))


def _clone_join(node: Join, left: PlanNode, right: PlanNode) -> Join:
    return Join(
        kind=node.kind,
        condition=node.condition,
        algorithm=node.algorithm,
        children=[left, right],
        output_columns=left.output_columns + right.output_columns,
    )


def _make_join_from_conds(
    conds: list[Expression], left: PlanNode, right: PlanNode, algorithm: str,
) -> Join:
    condition = _make_condition(conds)
    kind = JoinKind.THETA if condition is not None else JoinKind.CROSS
    return Join(
        kind=kind,
        condition=condition,
        algorithm=algorithm,
        children=[left, right],
        output_columns=left.output_columns + right.output_columns,
    )


def _reassociate(
    outer_join: Join,
    inner_join: Join,
    inner_left: PlanNode,
    inner_right: PlanNode,
    outer_left: PlanNode,
    outer_right_is_inner: bool,
) -> Join:
    all_conds = _flatten_conditions(inner_join.condition) + _flatten_conditions(outer_join.condition)
    if not all_conds:
        inner = Join(
            kind=JoinKind.CROSS,
            condition=None,
            algorithm=inner_join.algorithm,
            children=[inner_left, inner_right],
            output_columns=inner_left.output_columns + inner_right.output_columns,
        )
        outer_children = [outer_left, inner] if outer_right_is_inner else [inner, outer_left]
        return Join(
            kind=JoinKind.CROSS,
            condition=None,
            algorithm=outer_join.algorithm,
            children=outer_children,
            output_columns=outer_children[0].output_columns + outer_children[1].output_columns,
        )

    cond_tables = [_expr_tables(c) for c in all_conds]
    inner_tables = _source_tables(inner_left) | _source_tables(inner_right)
    if any(t is None for t in cond_tables) or not inner_tables:
        if outer_right_is_inner:
            inner = _clone_join(outer_join, inner_left, inner_right)
            return _clone_join(inner_join, outer_left, inner)
        inner = _clone_join(outer_join, inner_left, inner_right)
        return _clone_join(inner_join, inner, outer_left)

    inner_conds: list[Expression] = []
    outer_conds: list[Expression] = []
    for cond, tables in zip(all_conds, cond_tables):
        assert tables is not None
        if tables and tables <= inner_tables:
            inner_conds.append(cond)
        else:
            outer_conds.append(cond)

    inner = _make_join_from_conds(inner_conds, inner_left, inner_right, inner_join.algorithm)
    if outer_right_is_inner:
        return _make_join_from_conds(outer_conds, outer_left, inner, outer_join.algorithm)
    return _make_join_from_conds(outer_conds, inner, outer_left, outer_join.algorithm)


def _safe_cost(node: PlanNode, model: CostModel) -> float:
    try:
        return model.get_cost(node)
    except SemanticError:
        return float("inf")


def _commute(node: Join) -> Join:
    left, right = node.children
    return _clone_join(node, right, left)


def _rotate_left(node: Join) -> Join | None:
    left = node.children[0]
    if not _is_join(left):
        return None
    assert isinstance(left, Join)
    a, b = left.children
    c = node.children[1]
    return _reassociate(node, left, b, c, a, outer_right_is_inner=True)


def _rotate_right(node: Join) -> Join | None:
    right = node.children[1]
    if not _is_join(right):
        return None
    assert isinstance(right, Join)
    b, c = right.children
    a = node.children[0]
    return _reassociate(node, right, a, b, c, outer_right_is_inner=False)


class JoinOrderOptimizer:
    """Optimasi urutan join dengan commutativity (M2) dan associativity (M3)."""

    def __init__(self, max_passes: int = 8) -> None:
        self._max_passes = max_passes

    def reorder(self, plan: PlanNode, cost_model: CostModel) -> PlanNode:
        return self._reorder_recursive(plan, cost_model)

    def _reorder_recursive(
        self, node: PlanNode, model: CostModel, preserve_order: bool = False,
    ) -> PlanNode:
        if isinstance(node, Project):
            preserve_order = not node.columns
        node.children = [
            self._reorder_recursive(child, model, preserve_order)
            for child in node.children
        ]
        if not _is_join(node):
            return node
        assert isinstance(node, Join)
        return self._optimize_join(node, model, preserve_order)

    def _optimize_join(self, node: Join, model: CostModel, preserve_order: bool = False) -> Join:
        best = node
        best_cost = _safe_cost(best, model)

        for _ in range(self._max_passes):
            improved = False
            for candidate in self._generate_candidates(best):
                if preserve_order and _has_different_column_order(node, candidate):
                    continue
                c = _safe_cost(candidate, model)
                if c < best_cost - 1e-9:
                    best = candidate
                    best_cost = c
                    improved = True
            if not improved:
                break

        return best

    def _generate_candidates(self, node: Join) -> list[Join]:
        candidates: list[Join] = [_commute(node)]
        left, right = node.children

        left_variants = [node]
        if _is_join(left):
            assert isinstance(left, Join)
            left_variants.append(_clone_join(node, _commute(left), right))
        for variant in left_variants:
            rot_left = _rotate_left(variant)
            if rot_left is not None:
                candidates.append(rot_left)
                candidates.append(_commute(rot_left))
                inner = rot_left.children[1]
                if _is_join(inner):
                    assert isinstance(inner, Join)
                    rot_left_comm = _clone_join(rot_left, rot_left.children[0], _commute(inner))
                    candidates.append(rot_left_comm)
                    candidates.append(_commute(rot_left_comm))

        right_variants = [node]
        if _is_join(right):
            assert isinstance(right, Join)
            right_variants.append(_clone_join(node, left, _commute(right)))
        for variant in right_variants:
            rot_right = _rotate_right(variant)
            if rot_right is not None:
                candidates.append(rot_right)
                candidates.append(_commute(rot_right))
                inner = rot_right.children[0]
                if _is_join(inner):
                    assert isinstance(inner, Join)
                    rot_right_comm = _clone_join(rot_right, _commute(inner), rot_right.children[1])
                    candidates.append(rot_right_comm)
                    candidates.append(_commute(rot_right_comm))

        return candidates


def _leaf_tables(node: PlanNode) -> list[str]:
    # alias ikut dibandingkan supaya self-join tidak dianggap urutan yang sama
    return source_list(node)


def _has_different_column_order(original: Join, candidate: Join) -> bool:
    return _leaf_tables(original) != _leaf_tables(candidate)
