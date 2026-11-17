from __future__ import annotations

import unittest
from unittest.mock import Mock

from common.catalog import DataType
from common.expression import And, ColumnRef, Comparison, CompOp, Literal, Subquery
from common.plan import Filter, Limit, Project, TableScan
from query_processor.errors import ExecutionError, PlanError
from query_processor.subqueries import run_scalar
from tests.qp import test_bonus_translator as fixtures


class TestScalarBonus(unittest.TestCase):
    def setUp(self) -> None:
        self.case = fixtures.TestBonusTranslator()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def scalar(self, count: int, source=None, columns=None) -> Subquery:
        project = Project(columns=columns or [ColumnRef("student", "id")], aliases=["value"],
                          children=[source if source is not None else TableScan(table="student")])
        return Subquery(Limit(count=count, children=[project]))

    def outer(self, scalar) -> Project:
        return Project(columns=[ColumnRef("student", "id")], children=[Filter(
            condition=Comparison(CompOp.GE, ColumnRef("student", "id"), scalar),
            children=[TableScan(table="student")],
        )])

    def test_nested_limit_scalar_is_lazy_cached_and_keeps_the_outer_transaction(self) -> None:
        scalar = self.scalar(2)
        scalar.plan = Limit(count=1, children=[scalar.plan])
        executable = self.case.build(self.outer(scalar))
        self.case.sm.scan.assert_not_called()
        self.assertEqual(executable.execute(self.case.ctx).rows, [[1], [2], [3]])
        self.assertEqual(self.case.sm.scan.call_count, 2)
        self.assertTrue(all(call.args[0] == 7 for call in self.case.ccm.validate_object.call_args_list))

    def test_limit_zero_scalar_returns_typed_null_without_reading(self) -> None:
        scalar = self.scalar(0, TableScan(table="threshold"), [ColumnRef("threshold", "gpa")])
        self.assertEqual(run_scalar(scalar, self.case.ctx, self.case.translator.build_rows),
                         (None, DataType.FLOAT))
        self.case.sm.scan.assert_not_called()
        self.assertEqual(self.case.ccm.mock_calls, [])

    def test_limit_two_scalar_rejects_cardinality_without_reading_a_third_row(self) -> None:
        seen = []

        def scan(table):
            for row in self.case.rows[table]:
                seen.append(row.rid)
                yield row

        self.case.sm.scan.side_effect = scan
        with self.assertRaisesRegex(ExecutionError, "lebih dari satu baris"):
            run_scalar(self.scalar(2), self.case.ctx, self.case.translator.build_rows)
        self.assertEqual(len(seen), 2)

    def test_scalar_limit_zero_does_not_bypass_column_count_validation(self) -> None:
        scalar = self.scalar(0)
        scalar.plan.children[0].columns.append(ColumnRef("student", "name"))
        scalar.plan.children[0].aliases = []
        with self.assertRaisesRegex(PlanError, "satu kolom"):
            self.case.build(self.outer(scalar))
        self.case.sm.get_schema.assert_not_called()

    def test_correlated_reference_under_limit_is_rejected_before_reading(self) -> None:
        scalar = self.scalar(1, TableScan(table="course"), [ColumnRef("student", "id")])
        with self.assertRaises(PlanError):
            self.case.build(self.outer(scalar))
        self.case.sm.scan.assert_not_called()

    def test_short_circuit_does_not_execute_limited_scalar(self) -> None:
        plan = self.outer(self.scalar(2))
        plan.children[0].condition = And([
            Comparison(CompOp.EQ, Literal(0, DataType.INTEGER), Literal(1, DataType.INTEGER)),
            plan.children[0].condition,
        ])
        self.assertEqual(self.case.build(plan).execute(self.case.ctx).rows, [])
        self.case.sm.scan.assert_called_once_with("student")

    def test_invalid_scalar_limit_roots_are_rejected_before_building(self) -> None:
        build = Mock()
        cycle = Limit(count=1)
        cycle.children = [cycle]
        for root in (Limit(count=1), Limit(count=True, children=[Project(columns=[])]),
                     Limit(count=0, children=[TableScan(table="student")]), cycle):
            with self.subTest(node=type(root).__name__), self.assertRaises(PlanError):
                run_scalar(Subquery(root), self.case.ctx, build)
        build.assert_not_called()


if __name__ == "__main__":
    unittest.main()
