from __future__ import annotations

import copy
import unittest

from common.catalog import DataType, IndexInfo
from common.expression import BinaryOp, ArithOp, ColumnRef, Comparison, CompOp, Literal
from common.plan import (
    Delete, Filter, IndexScan, Join, JoinKind, Limit, Project, QueryKind, Sort, TableScan, Update,
)
from query_processor.errors import PlanError
from tests.qp import test_bonus_translator as fixtures


def project(source, *columns, aliases=None):
    return Project(columns=list(columns), aliases=aliases or [], children=[source])


class TestSelectBonusTranslator(unittest.TestCase):
    def setUp(self) -> None:
        self.case = fixtures.TestBonusTranslator()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def test_limit_alias_filter_sort_builds_lazily_without_mutating_plan(self) -> None:
        source = Filter(condition=Comparison(CompOp.GE, ColumnRef("s", "id"),
                                            Literal(2, DataType.INTEGER)),
                        children=[TableScan(table="student", alias="s")])
        plan = Limit(count=1, children=[project(
            Sort(column=ColumnRef("s", "id"), ascending=False, children=[source]),
            ColumnRef("s", "name"), ColumnRef("s", "id"), aliases=["nama", None],
        )])
        original = copy.deepcopy(plan)
        executable = self.case.build(plan)
        self.case.sm.scan.assert_not_called()
        self.assertEqual(self.case.ccm.mock_calls, [])
        result = executable.execute(self.case.ctx)
        self.assertEqual((result.columns, result.rows), (["nama", "id"], [["Cici", 3]]))
        self.assertEqual(plan, original)

    def test_chained_limits_keep_aliases_rids_and_can_be_reopened(self) -> None:
        plan = Limit(count=2, children=[Limit(count=1, children=[project(
            TableScan(table="student"), ColumnRef("student", "id"), aliases=["nim"],
        )])])
        operator = self.case.translator.build_rows(plan, self.case.ctx)
        for _ in range(2):
            operator.open(self.case.ctx)
            try:
                row = operator.next()
                self.assertEqual((row.values, row.rid), ((1,), self.case.rows["student"][0].rid))
                self.assertEqual(row.schema.output_names(), ["nim"])
                self.assertIsNone(operator.next())
            finally:
                operator.close()
        self.assertEqual(self.case.build(plan).execute(self.case.ctx).rows, [[1]])

    def test_limit_zero_preserves_header_without_opening_sort_or_scan(self) -> None:
        condition = Comparison(CompOp.GT,
                               BinaryOp(ArithOp.DIV, ColumnRef("student", "gpa"),
                                        Literal(0, DataType.INTEGER)), Literal(1, DataType.INTEGER))
        source = Sort(column=ColumnRef("student", "id"), children=[
            Filter(condition=condition, children=[TableScan(table="student")]),
        ])
        plan = Limit(count=0, children=[project(source, ColumnRef("student", "id"), aliases=["nim"])])
        result = self.case.build(plan).execute(self.case.ctx)
        self.assertEqual((result.columns, result.rows), (["nim"], []))
        self.case.sm.scan.assert_not_called()
        self.assertEqual(self.case.ccm.mock_calls, [])

    def test_invalid_limits_fail_before_catalog_access(self) -> None:
        select = project(TableScan(table="student"), ColumnRef("student", "id"))
        plans = [Limit(count=value, children=[select]) for value in (-1, True, False, 1.0, "1", None)]
        plans += [Limit(count=1, children=children) for children in ([], [select, select], (select,))]
        plans += [Limit(count=0, children=[TableScan(table="student")])]
        cycle = Limit(count=1)
        cycle.children = [cycle]
        plans.append(cycle)
        for plan in plans:
            with self.subTest(plan_type=type(plan).__name__), self.assertRaises(PlanError):
                self.case.build(plan)
        self.case.sm.get_schema.assert_not_called()

    def test_limit_zero_still_validates_references(self) -> None:
        plan = Limit(count=0, children=[project(TableScan(table="student"), ColumnRef("student", "missing"))])
        with self.assertRaises(PlanError):
            self.case.build(plan)
        self.case.sm.scan.assert_not_called()
        self.assertEqual(self.case.ccm.mock_calls, [])

    def test_labels_survive_filter_sort_limit_and_do_not_replace_bindings(self) -> None:
        inner = project(TableScan(table="student"), ColumnRef("student", "id"), aliases=["nim"])
        plan = Limit(count=2, children=[Sort(column=ColumnRef("student", "id"), children=[
            Filter(condition=Comparison(CompOp.GT, ColumnRef("student", "id"), Literal(1, DataType.INTEGER)),
                   children=[inner]),
        ])])
        operator = self.case.translator.build_rows(plan, self.case.ctx)
        self.assertEqual(operator.schema.output_names(), ["nim"])
        self.assertEqual(operator.schema.index_of(ColumnRef("student", "id")), 0)
        with self.assertRaises(PlanError):
            operator.schema.index_of(ColumnRef("student", "nim"))

    def test_duplicate_display_aliases_are_allowed_without_mutating_catalog(self) -> None:
        catalog = copy.deepcopy(self.case.catalog)
        plan = project(TableScan(table="student"), ColumnRef("student", "id"),
                       ColumnRef("student", "name"), aliases=["x", "x"])
        result = self.case.build(plan).execute(self.case.ctx)
        self.assertEqual((result.columns, result.rows[0]), (["x", "x"], [1, "Ada"]))
        self.assertEqual(self.case.catalog, catalog)

    def test_column_alias_is_not_a_source_reference(self) -> None:
        inner = project(TableScan(table="student"), ColumnRef("student", "id"), aliases=["nim"])
        for source in (
            Sort(column=ColumnRef("student", "nim"), children=[inner]),
            Filter(condition=Comparison(CompOp.EQ, ColumnRef("student", "nim"), Literal(1, DataType.INTEGER)),
                   children=[inner]),
        ):
            with self.subTest(node=type(source).__name__), self.assertRaises(PlanError):
                self.case.translator.build_rows(source, self.case.ctx)
        self.case.sm.scan.assert_not_called()

    def test_limit_and_aliases_preserve_physical_index_access(self) -> None:
        self.case.sm.index_lookup.return_value = [self.case.rows["student"][1].rid]
        source = IndexScan(table="student", alias="s", index=IndexInfo("idx", "student", "id"),
                           op=CompOp.EQ, key=2)
        plan = Limit(count=1, children=[project(source, ColumnRef("s", "id"), aliases=["nim"])])
        result = self.case.build(plan).execute(self.case.ctx)
        self.assertEqual((result.columns, result.rows), (["nim"], [[2]]))
        self.case.sm.index_lookup.assert_called_once_with("student", "id", 2)
        self.case.sm.fetch.assert_called_once_with("student", self.case.rows["student"][1].rid)
        self.case.sm.scan.assert_not_called()

    def test_limited_inner_join_is_rescanned_for_each_outer_block(self) -> None:
        inner = Limit(count=2, children=[project(TableScan(table="student", alias="b"), ColumnRef("b", "id"))])
        source = Join(kind=JoinKind.CROSS, children=[TableScan(table="student", alias="a"), inner])
        plan = project(source, ColumnRef("a", "id"), ColumnRef("b", "id"), aliases=["a", "b"])
        result = self.case.build(plan).execute(self.case.ctx)
        self.assertCountEqual(result.rows, [[a, b] for a in (1, 2, 3) for b in (1, 2)])

    def test_limit_does_not_hide_duplicate_source_aliases(self) -> None:
        inner = Limit(count=0, children=[project(TableScan(table="student", alias="s"), ColumnRef("s", "id"))])
        source = Join(kind=JoinKind.CROSS, children=[TableScan(table="student", alias="s"), inner])
        with self.assertRaisesRegex(PlanError, "sumber berulang"):
            self.case.build(project(source, ColumnRef("s", "id")))
        self.case.sm.get_schema.assert_not_called()

    def test_limit_cannot_be_used_as_a_dml_target_source(self) -> None:
        source = Limit(count=1, children=[TableScan(table="student")])
        for node, kind in (
            (Delete(table="student", children=[source]), QueryKind.DELETE),
            (Update(table="student", column="id", expression=Literal(9, DataType.INTEGER),
                    children=[source]), QueryKind.UPDATE),
        ):
            with self.subTest(kind=kind), self.assertRaises(PlanError):
                self.case.build(node, kind)
        self.assertEqual(self.case.sm.mock_calls, [])


if __name__ == "__main__":
    unittest.main()
