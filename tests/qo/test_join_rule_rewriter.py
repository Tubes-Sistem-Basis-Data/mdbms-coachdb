"""QO-11: unit test JoinRuleRewriter."""

from __future__ import annotations

import unittest

from common.expression import And, Comparison, CompOp, ColumnRef, Literal
from common.catalog import DataType
from common.plan import Filter, Join, JoinKind, Project, TableScan, PlanNode
from query_optimizer.join_rule_rewriter import JoinRuleRewriter


def _scan(table: str) -> TableScan:
    return TableScan(table=table)


def _cross(left: PlanNode, right: PlanNode) -> Join:
    return Join(kind=JoinKind.CROSS, children=[left, right])


def _theta(cond, left: PlanNode, right: PlanNode) -> Join:
    return Join(kind=JoinKind.THETA, condition=cond, children=[left, right])


def _col(table: str | None, column: str) -> ColumnRef:
    return ColumnRef(table=table, column=column)


def _lit(val: int) -> Literal:
    return Literal(value=val, type=DataType.INTEGER)


def _eq(left, right) -> Comparison:
    return Comparison(op=CompOp.EQ, left=left, right=right)


def _and(*conds) -> And:
    return And(conditions=list(conds))


class TestJoinRuleRewriter(unittest.TestCase):

    def setUp(self) -> None:
        self.rw = JoinRuleRewriter()

    def apply(self, plan: PlanNode) -> PlanNode:
        return self.rw.apply(plan)

    def test_cross_with_join_condition_becomes_theta(self) -> None:
        cond = _eq(_col("a", "id"), _col("b", "a_id"))
        plan = Filter(condition=cond, children=[_cross(_scan("a"), _scan("b"))])
        result = self.apply(plan)
        self.assertIsInstance(result, Join)
        self.assertEqual(result.kind, JoinKind.THETA)
        self.assertEqual(result.condition, cond)
        self.assertEqual(result.children[0].table, "a")
        self.assertEqual(result.children[1].table, "b")

    def test_mixed_conditions_split_correctly(self) -> None:
        join_cond = _eq(_col("a", "id"), _col("b", "id"))
        rest_cond = Comparison(op=CompOp.GT, left=_col("a", "val"), right=_lit(0))
        plan = Filter(condition=_and(join_cond, rest_cond), children=[_cross(_scan("a"), _scan("b"))])
        result = self.apply(plan)
        self.assertIsInstance(result, Filter)
        self.assertEqual(result.condition, rest_cond)
        inner = result.children[0]
        self.assertIsInstance(inner, Join)
        self.assertEqual(inner.kind, JoinKind.THETA)
        self.assertEqual(inner.condition, join_cond)

    def test_single_side_filter_keeps_cross(self) -> None:
        cond = _eq(_col("a", "val"), _lit(1))
        plan = Filter(condition=cond, children=[_cross(_scan("a"), _scan("b"))])
        result = self.apply(plan)
        self.assertIsInstance(result, Filter)
        self.assertEqual(result.children[0].kind, JoinKind.CROSS)

    def test_cross_without_filter_unchanged(self) -> None:
        plan = _cross(_scan("a"), _scan("b"))
        result = self.apply(plan)
        self.assertIsInstance(result, Join)
        self.assertEqual(result.kind, JoinKind.CROSS)

    def test_explicit_theta_join_unchanged(self) -> None:
        cond = _eq(_col("a", "id"), _col("b", "id"))
        plan = _theta(cond, _scan("a"), _scan("b"))
        result = self.apply(plan)
        self.assertEqual(result.kind, JoinKind.THETA)
        self.assertEqual(result.condition, cond)

    def test_natural_join_unchanged(self) -> None:
        plan = Join(kind=JoinKind.NATURAL, children=[_scan("a"), _scan("b")])
        result = self.apply(plan)
        self.assertEqual(result.kind, JoinKind.NATURAL)

    def test_recursive_nested_cross(self) -> None:
        cond_ab = _eq(_col("a", "id"), _col("b", "id"))
        cond_bc = _eq(_col("b", "id"), _col("c", "id"))
        outer_cross = _cross(_cross(_scan("a"), _scan("b")), _scan("c"))
        plan = Filter(condition=_and(cond_ab, cond_bc), children=[outer_cross])
        result = self.apply(plan)
        self.assertIsInstance(result, Filter)
        self.assertEqual(result.condition, cond_ab)
        theta_outer = result.children[0]
        self.assertEqual(theta_outer.kind, JoinKind.THETA)
        self.assertEqual(theta_outer.condition, cond_bc)
        self.assertEqual(theta_outer.children[0].kind, JoinKind.CROSS)

    def test_nested_and_is_flattened(self) -> None:
        join_cond = _eq(_col("a", "id"), _col("b", "id"))
        rest1 = Comparison(op=CompOp.GT, left=_col("a", "x"), right=_lit(0))
        rest2 = Comparison(op=CompOp.LT, left=_col("b", "y"), right=_lit(10))
        nested = _and(_and(join_cond, rest1), rest2)
        plan = Filter(condition=nested, children=[_cross(_scan("a"), _scan("b"))])
        result = self.apply(plan)
        inner = result.children[0] if isinstance(result, Filter) else result
        self.assertEqual(inner.kind, JoinKind.THETA)

    def test_literal_comparison_not_treated_as_join(self) -> None:
        join_cond = _eq(_col("a", "id"), _col("b", "id"))
        literal_cond = _eq(_lit(1), _lit(1))
        plan = Filter(condition=_and(join_cond, literal_cond), children=[_cross(_scan("a"), _scan("b"))])
        result = self.apply(plan)
        self.assertIsInstance(result, Filter)
        self.assertEqual(result.condition, literal_cond)
        self.assertEqual(result.children[0].kind, JoinKind.THETA)
        self.assertEqual(result.children[0].condition, join_cond)

    def test_unqualified_column_not_treated_as_join(self) -> None:
        cond = _eq(_col(None, "id"), _lit(1))
        plan = Filter(condition=cond, children=[_cross(_scan("a"), _scan("b"))])
        result = self.apply(plan)
        self.assertIsInstance(result, Filter)
        self.assertEqual(result.children[0].kind, JoinKind.CROSS)

    def test_rewrite_is_idempotent(self) -> None:
        cond = _eq(_col("a", "id"), _col("b", "id"))
        plan = Filter(condition=cond, children=[_cross(_scan("a"), _scan("b"))])
        once = self.apply(plan)
        twice = self.apply(once)
        self.assertEqual(twice.kind, JoinKind.THETA)

    def test_non_join_nodes_passthrough(self) -> None:
        project = Project(columns=[_col("a", "id")], children=[_scan("a")])
        result = self.apply(project)
        self.assertIsInstance(result, Project)
        self.assertIsInstance(result.children[0], TableScan)

    def test_filter_over_tablescan_unchanged(self) -> None:
        cond = _eq(_col("a", "id"), _lit(5))
        plan = Filter(condition=cond, children=[_scan("a")])
        result = self.apply(plan)
        self.assertIsInstance(result, Filter)
        self.assertIsInstance(result.children[0], TableScan)

    def test_all_join_predicates_no_remaining_filter(self) -> None:
        cond1 = _eq(_col("a", "id"), _col("b", "id"))
        cond2 = _eq(_col("a", "x"), _col("b", "x"))
        plan = Filter(condition=_and(cond1, cond2), children=[_cross(_scan("a"), _scan("b"))])
        result = self.apply(plan)
        self.assertIsInstance(result, Join)
        self.assertEqual(result.kind, JoinKind.THETA)
        self.assertIn(cond1, result.condition.conditions)
        self.assertIn(cond2, result.condition.conditions)

    def test_output_columns_propagated_to_theta(self) -> None:
        cols = [_col("a", "id"), _col("b", "id")]
        cond = _eq(_col("a", "id"), _col("b", "id"))
        cross = Join(kind=JoinKind.CROSS, children=[_scan("a"), _scan("b")], output_columns=cols)
        plan = Filter(condition=cond, children=[cross])
        result = self.apply(plan)
        self.assertIsInstance(result, Join)
        self.assertEqual(result.output_columns, cols)

    def test_output_columns_propagated_to_remaining_filter(self) -> None:
        filter_cols = [_col("a", "id"), _col("b", "id")]
        cross_cols = [_col("a", "id"), _col("b", "id")]
        join_cond = _eq(_col("a", "id"), _col("b", "id"))
        rest_cond = Comparison(op=CompOp.GT, left=_col("a", "val"), right=_lit(0))
        cross = Join(kind=JoinKind.CROSS, children=[_scan("a"), _scan("b")], output_columns=cross_cols)
        plan = Filter(condition=_and(join_cond, rest_cond), children=[cross], output_columns=filter_cols)
        result = self.apply(plan)
        self.assertIsInstance(result, Filter)
        self.assertEqual(result.output_columns, filter_cols)
        self.assertEqual(result.children[0].output_columns, cross_cols)

    def test_source_tables_via_output_columns_enables_alias_awareness(self) -> None:
        # Simulasi post-binder: output_columns anak Cross berisi nama tabel nyata
        # (bukan alias), _source_tables harus tetap bekerja via output_columns.
        left_scan = TableScan(table="student", output_columns=[_col("student", "id")])
        right_scan = TableScan(table="enrollment", output_columns=[_col("enrollment", "student_id")])
        cross = Join(kind=JoinKind.CROSS, children=[left_scan, right_scan],
                     output_columns=[_col("student", "id"), _col("enrollment", "student_id")])
        cond = _eq(_col("student", "id"), _col("enrollment", "student_id"))
        plan = Filter(condition=cond, children=[cross])
        result = self.apply(plan)
        self.assertIsInstance(result, Join)
        self.assertEqual(result.kind, JoinKind.THETA)


if __name__ == "__main__":
    unittest.main()

