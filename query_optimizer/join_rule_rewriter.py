"""Mengubah cartesian product dengan seleksi menjadi join (QO-11)."""

from __future__ import annotations

from common.expression import And, Comparison, ColumnRef, Expression
from common.plan import Filter, Join, JoinKind, PlanNode
from query_optimizer.plan_utils import source_list


def _tables_in_expr(expr: Expression) -> set[str]:
    if isinstance(expr, ColumnRef):
        return {expr.table} if expr.table else set()
    if isinstance(expr, Comparison):
        return _tables_in_expr(expr.left) | _tables_in_expr(expr.right)
    if isinstance(expr, And):
        result: set[str] = set()
        for c in expr.conditions:
            result |= _tables_in_expr(c)
        return result
    return set()


def _source_tables(node: PlanNode) -> set[str]:
    if node.output_columns:
        return {ref.table for ref in node.output_columns if ref.table}
    return set(source_list(node))


def _split_conditions(
    conditions: list[Expression],
    left_tables: set[str],
    right_tables: set[str],
) -> tuple[list[Expression], list[Expression]]:
    join_preds: list[Expression] = []
    rest_preds: list[Expression] = []
    for cond in conditions:
        tables = _tables_in_expr(cond)
        if tables & left_tables and tables & right_tables:
            join_preds.append(cond)
        else:
            rest_preds.append(cond)
    return join_preds, rest_preds


def _make_condition(preds: list[Expression]) -> Expression:
    assert preds
    return preds[0] if len(preds) == 1 else And(conditions=preds)


def _flatten_conditions(expr: Expression) -> list[Expression]:
    if isinstance(expr, And):
        result: list[Expression] = []
        for c in expr.conditions:
            result.extend(_flatten_conditions(c))
        return result
    return [expr]


class JoinRuleRewriter:
    """Mengubah Filter(Cross(A, B)) menjadi Theta-Join(A, B) (QO-11)."""

    def apply(self, plan: PlanNode) -> PlanNode:
        plan.children = [self.apply(child) for child in plan.children]
        return self._try_rewrite(plan)

    def _try_rewrite(self, node: PlanNode) -> PlanNode:
        if not isinstance(node, Filter):
            return node

        cross = node.children[0]
        if not isinstance(cross, Join) or cross.kind is not JoinKind.CROSS:
            return node

        conditions = _flatten_conditions(node.condition)
        left_tables = _source_tables(cross.children[0])
        right_tables = _source_tables(cross.children[1])
        join_preds, rest_preds = _split_conditions(conditions, left_tables, right_tables)

        if not join_preds:
            return node

        join_cond = _make_condition(join_preds)
        theta = Join(
            kind=JoinKind.THETA,
            condition=join_cond,
            children=cross.children,
            estimated_rows=cross.estimated_rows,
            estimated_cost=cross.estimated_cost,
            output_columns=cross.output_columns,
        )

        if not rest_preds:
            return theta

        return Filter(condition=_make_condition(rest_preds), children=[theta], output_columns=node.output_columns)

