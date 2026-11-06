"""Penukaran urutan dan pengelompokan join (QO-12).

M2: Join(A, B) <-> Join(B, A)
M3: Join(Join(A,B), C) <-> Join(A, Join(B,C))
"""

from __future__ import annotations

from common.errors import SemanticError
from common.plan import Join, JoinKind, PlanNode, Project
from .cost_model import CostModel


def _is_join(node: PlanNode) -> bool:
    return isinstance(node, Join) and node.kind in (JoinKind.THETA, JoinKind.CROSS)


def _clone_join(node: Join, left: PlanNode, right: PlanNode) -> Join:
    return Join(
        kind=node.kind,
        condition=node.condition,
        algorithm=node.algorithm,
        children=[left, right],
        output_columns=left.output_columns + right.output_columns,
    )


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
    a, b = left.children
    c = node.children[1]
    inner = _clone_join(node, b, c)
    return _clone_join(left, a, inner)


def _rotate_right(node: Join) -> Join | None:
    right = node.children[1]
    if not _is_join(right):
        return None
    b, c = right.children
    a = node.children[0]
    inner = _clone_join(node, a, b)
    return _clone_join(right, inner, c)


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
        candidates: list[Join] = []
        candidates.append(_commute(node))
        rot_left = _rotate_left(node)
        if rot_left is not None:
            candidates.append(rot_left)
            candidates.append(_commute(rot_left))
        rot_right = _rotate_right(node)
        if rot_right is not None:
            candidates.append(rot_right)
            candidates.append(_commute(rot_right))
        return candidates


def _leaf_tables(node: PlanNode) -> list[str]:
    from common.plan import TableScan, IndexScan
    if isinstance(node, (TableScan, IndexScan)):
        return [node.table]
    result: list[str] = []
    for child in node.children:
        result.extend(_leaf_tables(child))
    return result


def _has_different_column_order(original: Join, candidate: Join) -> bool:
    return _leaf_tables(original) != _leaf_tables(candidate)
