from __future__ import annotations

import copy
import unittest
from unittest.mock import Mock

from common.catalog import ColumnSchema, DataType, IndexInfo, TableSchema
from common.expression import ArithOp, BinaryOp, ColumnRef, Comparison, CompOp, Literal, Subquery
from common.interfaces import ConcurrencyControlManager, StorageManager
from common.plan import (
    Delete, Filter, IndexScan, Join, JoinKind, ParsedQuery, Project, QueryKind, Sort, TableScan, Update,
)
from common.storage import RID, Row
from common.transaction import AccessResponse, AccessStatus
from query_processor.context import ExecContext
from query_processor.errors import PlanError
from query_processor.translator import PlanTranslator


def ref(table: str, name: str = "id") -> ColumnRef:
    return ColumnRef(table, name)


def select(source, *columns) -> Project:
    return Project(columns=list(columns), children=[source])


class TestBonusTranslator(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = {
            "student": TableSchema("student", [
                ColumnSchema("id", DataType.INTEGER), ColumnSchema("name", DataType.VARCHAR, 50),
                ColumnSchema("gpa", DataType.FLOAT),
            ]),
            "course": TableSchema("course", [
                ColumnSchema("id", DataType.INTEGER), ColumnSchema("title", DataType.VARCHAR, 50),
            ]),
            "threshold": TableSchema("threshold", [ColumnSchema("gpa", DataType.FLOAT)]),
        }
        self.rows = {
            "student": [Row(RID("student", 0, i), values) for i, values in enumerate(
                ([1, "Ada", 3.0], [2, "Budi", 4.0], [3, "Cici", 2.5])
            )],
            "course": [Row(RID("course", 0, 0), [1, "DB"])],
            "threshold": [Row(RID("threshold", 0, 0), [3.2])],
        }
        self.sm = Mock(spec=StorageManager)
        self.sm.get_schema.side_effect = lambda table: copy.deepcopy(self.catalog[table])
        self.sm.scan.side_effect = lambda table: iter(copy.deepcopy(self.rows[table]))
        self.sm.fetch.side_effect = lambda table, rid: next(
            (copy.deepcopy(row) for row in self.rows[table] if row.rid == rid), None
        )
        self.ccm = Mock(spec=ConcurrencyControlManager)
        self.ccm.validate_object.return_value = AccessResponse(AccessStatus.ALLOWED)
        self.ctx = ExecContext(7, self.sm, self.ccm)
        self.translator = PlanTranslator()

    def build(self, plan, kind=QueryKind.SELECT):
        return self.translator.build(ParsedQuery("manual plan", kind, plan), self.ctx)

    def test_self_join_keeps_aliases_separate_and_accesses_physical_table(self) -> None:
        join = Join(kind=JoinKind.THETA, condition=Comparison(CompOp.EQ, ref("a"), ref("b")),
                    children=[TableScan(table="student", alias="a"), TableScan(table="student", alias="b")])
        plan = select(join, ref("a", "name"), ref("b", "name"))
        original = copy.deepcopy(plan)
        executable = self.build(plan)
        self.sm.get_schema.assert_called_once_with("student")
        self.sm.scan.assert_not_called()
        self.assertEqual(self.ccm.mock_calls, [])

        result = executable.execute(self.ctx)

        self.assertCountEqual(result.rows, [["Ada", "Ada"], ["Budi", "Budi"], ["Cici", "Cici"]])
        self.assertEqual(plan, original)
        self.assertTrue(all(call.args == ("student",) for call in self.sm.scan.call_args_list))
        self.assertTrue(all(call.args[0] == 7 and call.args[1].table == "student"
                            for call in self.ccm.validate_object.call_args_list))

    def test_filter_sort_and_projection_resolve_the_alias(self) -> None:
        source = Filter(condition=Comparison(CompOp.GE, ref("s"), Literal(2, DataType.INTEGER)),
                        children=[TableScan(table="student", alias="s")])
        plan = select(Sort(column=ref("s"), ascending=False, children=[source]), ref("s", "name"))
        self.assertEqual(self.build(plan).execute(self.ctx).rows, [["Cici"], ["Budi"]])
        physical = self.translator.build_rows(TableScan(table="student"), self.ctx)
        self.assertTrue(all(column.qualifier == "student" for column in physical.schema.columns))

    def test_physical_prefix_is_hidden_by_alias_in_every_expression_position(self) -> None:
        aliased = TableScan(table="student", alias="s")
        plans = (
            select(aliased, ref("student")),
            select(Filter(condition=Comparison(CompOp.EQ, ref("student"), Literal(1, DataType.INTEGER)),
                          children=[aliased]), ref("s")),
            select(Sort(column=ref("student"), children=[aliased]), ref("s")),
            select(Join(kind=JoinKind.THETA, condition=Comparison(CompOp.EQ, ref("student"), ref("c")),
                        children=[aliased, TableScan(table="course", alias="c")]), ref("s")),
        )
        for plan in plans:
            with self.subTest(plan=plan), self.assertRaises(PlanError):
                self.build(plan)
        self.sm.scan.assert_not_called()
        self.assertEqual(self.ccm.mock_calls, [])

    def test_index_scan_alias_uses_physical_lookup_and_fetch(self) -> None:
        self.sm.index_lookup.return_value = [self.rows["student"][1].rid]
        source = IndexScan(table="student", alias="s", index=IndexInfo("idx", "student", "id"),
                           op=CompOp.EQ, key=2)

        result = self.build(select(source, ref("s"), ref("s", "name"))).execute(self.ctx)

        self.assertEqual(result.rows, [[2, "Budi"]])
        self.sm.index_lookup.assert_called_once_with("student", "id", 2)
        self.sm.fetch.assert_called_once_with("student", self.rows["student"][1].rid)
        self.sm.scan.assert_not_called()
        self.assertTrue(all(call.args[1].table == "student" for call in self.ccm.validate_object.call_args_list))

    def test_index_range_alias_keeps_strict_boundary(self) -> None:
        self.sm.index_range.return_value = [row.rid for row in self.rows["student"]]
        source = IndexScan(table="student", alias="s", index=IndexInfo("idx", "student", "id"),
                           op=CompOp.GT, low=2)
        self.assertEqual(self.build(select(source, ref("s"))).execute(self.ctx).rows, [[3]])
        self.sm.index_range.assert_called_once_with("student", "id", 2, None)

    def test_natural_join_exports_only_alias_qualifiers(self) -> None:
        join = Join(kind=JoinKind.NATURAL, children=[TableScan(table="student", alias="s"),
                                                   TableScan(table="course", alias="c")])
        operator = self.translator.build_rows(join, self.ctx)
        self.assertEqual(operator.schema.index_of(ref("s")), operator.schema.index_of(ref("c")))
        for table in ("student", "course"):
            with self.subTest(table=table), self.assertRaises(PlanError):
                operator.schema.index_of(ref(table))
        result = self.build(select(join, ref("c"), ref("s", "name"))).execute(self.ctx)
        self.assertEqual(result.rows, [[1, "Ada"]])

    def test_invalid_or_duplicate_source_aliases_fail_before_catalog_access(self) -> None:
        for alias in ("", " ", False, 3):
            with self.subTest(alias=alias), self.assertRaises(PlanError):
                self.build(select(TableScan(table="student", alias=alias), ref("s")))
        for children in (
            [TableScan(table="student"), TableScan(table="student")],
            [TableScan(table="student", alias="s"), TableScan(table="course", alias="s")],
            [TableScan(table="student", alias="course"), TableScan(table="course")],
        ):
            with self.subTest(children=children), self.assertRaisesRegex(PlanError, "sumber berulang"):
                self.build(select(Join(kind=JoinKind.CROSS, children=children), ref("student")))
        self.sm.get_schema.assert_not_called()

    def test_column_alias_parameters_are_not_silently_ignored(self) -> None:
        for aliases in (["nim", "extra"], [""], [False], None, ["nim"]):
            with self.subTest(aliases=aliases), self.assertRaises(PlanError):
                self.build(Project(columns=[ref("student")], aliases=aliases,
                                   children=[TableScan(table="student")]))

    def test_dml_alias_targets_are_rejected_without_mutation(self) -> None:
        source = TableScan(table="student", alias="s")
        for node, kind in (
            (Delete(table="student", children=[source]), QueryKind.DELETE),
            (Update(table="student", column="id", expression=Literal(1, DataType.INTEGER),
                    children=[source]), QueryKind.UPDATE),
        ):
            with self.subTest(kind=kind), self.assertRaises(PlanError):
                self.build(node, kind)
        self.assertEqual(self.sm.mock_calls, [])

    def test_scalar_build_is_lazy_and_resolves_inner_scope_independently(self) -> None:
        scalar = Subquery(select(TableScan(table="threshold"), ref("threshold", "gpa")))
        plan = select(Filter(condition=Comparison(CompOp.GT, ref("s", "gpa"), scalar),
                             children=[TableScan(table="student", alias="s")]), ref("s"))
        original = copy.deepcopy(plan)
        executable = self.build(plan)
        self.assertTrue(all(call[0] == "get_schema" for call in self.sm.mock_calls))
        self.assertEqual(self.ccm.mock_calls, [])
        self.rows["threshold"][0].values = [2.0]

        self.assertEqual(executable.execute(self.ctx).rows, [[1], [2], [3]])
        self.assertEqual(plan, original)
        self.assertEqual(sum(call.args == ("threshold",) for call in self.sm.scan.call_args_list), 1)
        self.assertTrue(all(call.args[0] == 7 for call in self.ccm.validate_object.call_args_list))

    def test_correlated_scalar_reference_is_rejected_before_reading(self) -> None:
        for inner in (
            select(TableScan(table="course"), ref("s", "gpa")),
            select(Filter(condition=Comparison(CompOp.EQ, ref("course"), ref("s")),
                          children=[TableScan(table="course")]), ref("course")),
        ):
            outer = select(Filter(condition=Comparison(CompOp.EQ, ref("s"), Subquery(inner)),
                                  children=[TableScan(table="student", alias="s")]), ref("s"))
            with self.subTest(inner=inner), self.assertRaises(PlanError):
                self.build(outer)
        self.sm.scan.assert_not_called()
        self.assertEqual(self.ccm.mock_calls, [])

    def test_cross_plan_scalar_cycle_is_rejected_before_catalog_access(self) -> None:
        plan = select(TableScan(table="student"), ref("student"))
        plan.children = [Filter(condition=Comparison(CompOp.EQ, ref("student"), Subquery(plan)),
                                children=[TableScan(table="student")])]
        with self.assertRaisesRegex(PlanError, "siklus"):
            self.build(plan)
        self.sm.get_schema.assert_not_called()

    def test_multiple_scalar_columns_and_arithmetic_position_are_rejected(self) -> None:
        inner = select(TableScan(table="student"), ref("student"))
        for operand in (
            Subquery(select(TableScan(table="student"), ref("student"), ref("student", "name"))),
            BinaryOp(ArithOp.ADD, Subquery(inner), Literal(1, DataType.INTEGER)),
        ):
            plan = select(Filter(condition=Comparison(CompOp.EQ, ref("student"), operand),
                                 children=[TableScan(table="student")]), ref("student"))
            with self.subTest(operand=operand), self.assertRaises(PlanError):
                self.build(plan)
        self.sm.get_schema.assert_not_called()


if __name__ == "__main__":
    unittest.main()
