from __future__ import annotations

import unittest

from common.catalog import DataType
from common.expression import ColumnRef, Comparison, CompOp, Literal
from query_processor.errors import PlanError
from query_processor.operators.filter import FilterOp
from query_processor.operators.project import ProjectOp, SelectExecution
from tests.qp.fakes import ROWS, SCHEMA, FakeCtx, FakeSM, FakeSource, drain

ID = ColumnRef("t", "id")
NAME = ColumnRef("t", "name")


class TestProjectAliases(unittest.TestCase):
    def test_mixed_aliases_change_only_output_names(self):
        op = ProjectOp(FakeSource(), [ID, NAME], ["nim", None])
        self.assertEqual(op.schema.output_names(), ["nim", "name"])
        self.assertEqual([b.name for b in op.schema.columns], ["id", "name"])
        self.assertEqual(op.schema.index_of(ID), 0)

    def test_values_and_rid_unchanged(self):
        op = ProjectOp(FakeSource(), [NAME, ID], ["nama", "nim"])
        op.open(FakeCtx(FakeSM()))
        rows = drain(op)
        op.close()
        self.assertEqual(rows[1].values, ("n1", 1))
        self.assertEqual(rows[1].rid, ROWS[1].rid)
        self.assertIs(rows[1].schema, op.schema)

    def test_without_aliases_uses_source_names(self):
        for aliases in (None, [], [None, None]):
            with self.subTest(aliases=aliases):
                op = ProjectOp(FakeSource(), [NAME, ID], aliases)
                self.assertIsNone(op.schema.labels)
                self.assertEqual(op.schema.output_names(), ["name", "id"])

    def test_old_constructor_still_works(self):
        op = ProjectOp(FakeSource(), [NAME, ID])
        self.assertEqual(op.schema.output_names(), ["name", "id"])

    def test_duplicate_labels_allowed(self):
        op = ProjectOp(FakeSource(), [ID, NAME], ["x", "x"])
        self.assertEqual(op.schema.output_names(), ["x", "x"])

    def test_invalid_aliases_rejected(self):
        for aliases in (["nim"], ["nim", None, "extra"], ["", None], ["  ", None], [5, None], "nim", ("nim", None)):
            with self.subTest(aliases=aliases), self.assertRaises(PlanError):
                ProjectOp(FakeSource(), [ID, NAME], aliases)

    def test_child_schema_not_mutated(self):
        child = FakeSource()
        before = list(child.schema.columns)
        ProjectOp(child, [ID], ["nim"])
        self.assertIs(child.schema, SCHEMA)
        self.assertEqual(child.schema.columns, before)
        self.assertIsNone(child.schema.labels)


class TestSelectHeader(unittest.TestCase):
    def test_header_uses_aliases(self):
        root = ProjectOp(FakeSource(), [ID, NAME], ["nim", None])
        result = SelectExecution(root).execute(FakeCtx(FakeSM()))
        self.assertEqual(result.columns, ["nim", "name"])
        self.assertEqual(result.rows[0], [0, "n0"])

    def test_header_kept_when_result_empty(self):
        root = ProjectOp(FakeSource([]), [ID], ["nim"])
        result = SelectExecution(root).execute(FakeCtx(FakeSM()))
        self.assertEqual(result.columns, ["nim"])
        self.assertEqual(result.rows, [])

    def test_header_when_root_is_not_project(self):
        project = ProjectOp(FakeSource(), [ID, NAME], ["nim", "nama"])
        root = FilterOp(project, Comparison(CompOp.GE, ID, Literal(3, DataType.INTEGER)))
        result = SelectExecution(root).execute(FakeCtx(FakeSM()))
        self.assertEqual(result.columns, ["nim", "nama"])
        self.assertEqual(result.rows, [[3, "n3"]])


if __name__ == "__main__":
    unittest.main()