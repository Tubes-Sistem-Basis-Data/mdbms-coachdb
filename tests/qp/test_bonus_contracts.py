from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from common.catalog import ColumnSchema, DataType, TableSchema
from common.expression import ColumnRef, Comparison, CompOp, Literal
from common.interfaces import ConcurrencyControlManager, StorageManager
from common.plan import ParsedQuery, Project, QueryKind, TableScan
from common.storage import RID
from common.transaction import NO_RECORD, AccessResponse, AccessStatus, Action, ChangeOp, DataChange
from query_processor.context import ExecContext
from query_processor.errors import ExecutionError, PlanError
from query_processor.operators.filter import FilterOp
from query_processor.operators.project import ProjectOp
from query_processor.operators.sort import SortOp
from query_processor.translator import PlanTranslator
from query_processor.tuple import ColumnBinding, ExecRow, OutputSchema
from tests.qp.fakes import FakeCtx, FakeSM, FakeSource, drain


class TestOutputNames(unittest.TestCase):
    def setUp(self) -> None:
        self.columns = [
            ColumnBinding("student", "id", DataType.INTEGER),
            ColumnBinding("student", "name", DataType.VARCHAR),
        ]

    def test_old_constructor_returns_source_names_in_order(self) -> None:
        schema = OutputSchema(self.columns)
        names = schema.output_names()

        self.assertEqual(names, ["id", "name"])
        names[0] = "changed"
        self.assertEqual(schema.output_names(), ["id", "name"])

    def test_labels_change_output_names_without_changing_resolution(self) -> None:
        schema = OutputSchema(self.columns, labels=("nim", "nama"))

        self.assertEqual(schema.output_names(), ["nim", "nama"])
        self.assertIs(schema.columns[0], self.columns[0])
        self.assertEqual(schema.index_of(ColumnRef("student", "id")), 0)
        self.assertEqual(schema.index_of(ColumnRef(None, "name")), 1)
        for ref in (ColumnRef("student", "nim"), ColumnRef(None, "nama")):
            with self.subTest(ref=ref), self.assertRaises(PlanError):
                schema.index_of(ref)

    def test_label_results_are_independent_lists(self) -> None:
        schema = OutputSchema(self.columns, labels=("nim", "nama"))
        names = schema.output_names()
        names.clear()

        self.assertEqual(schema.output_names(), ["nim", "nama"])
        self.assertEqual(schema.labels, ("nim", "nama"))
        self.assertEqual(OutputSchema(self.columns).output_names(), ["id", "name"])

    def test_repeated_display_names_are_allowed(self) -> None:
        schema = OutputSchema(self.columns, labels=("nilai", "nilai"))
        self.assertEqual(schema.output_names(), ["nilai", "nilai"])

    def test_labels_preserve_natural_join_qualifiers(self) -> None:
        binding = ColumnBinding("student", "id", DataType.INTEGER, ("enroll",))
        schema = OutputSchema([binding], labels=("nim",))

        self.assertEqual(schema.index_of(ColumnRef("student", "id")), 0)
        self.assertEqual(schema.index_of(ColumnRef("enroll", "id")), 0)
        self.assertEqual(binding.alternate_qualifiers, ("enroll",))

    def test_invalid_label_type_length_or_name_is_rejected(self) -> None:
        for labels in ("nim", ["nim", "nama"], (), ("nim",), ("nim", "nama", "extra"),
                       (None, "nama"), (1, "nama"), ("", "nama"), ("  ", "nama")):
            with self.subTest(labels=labels), self.assertRaises(PlanError):
                OutputSchema(self.columns, labels=labels)

    def test_changed_schema_cannot_silently_return_mismatched_labels(self) -> None:
        schema = OutputSchema(list(self.columns), labels=("nim", "nama"))
        schema.columns.pop()

        with self.assertRaises(PlanError):
            schema.output_names()

    def test_empty_schema_and_old_exec_row_constructor_remain_valid(self) -> None:
        self.assertEqual(OutputSchema([]).output_names(), [])
        self.assertEqual(OutputSchema([], labels=()).output_names(), [])
        schema = OutputSchema(self.columns)
        rid = RID("student", 0, 1)
        row = ExecRow((1, "Ada"), schema, rid)

        self.assertEqual(row.values, (1, "Ada"))
        self.assertIs(row.schema, schema)
        self.assertIs(row.rid, rid)
        self.assertIsNone(ExecRow((1, "Ada"), schema).rid)

    def test_filter_and_sort_keep_source_labels_and_rid(self) -> None:
        source = FakeSource()
        source.schema = OutputSchema(list(source.schema.columns), labels=("nim", "nama"))
        condition = Comparison(CompOp.GE, ColumnRef("t", "id"), Literal(2, DataType.INTEGER))
        filtered = FilterOp(source, condition)
        sorted_rows = SortOp(filtered, ColumnRef("t", "id"), ascending=False)
        try:
            sorted_rows.open(FakeCtx(FakeSM()))
            rows = drain(sorted_rows)
        finally:
            sorted_rows.close()

        self.assertIs(filtered.schema, source.schema)
        self.assertIs(sorted_rows.schema, source.schema)
        self.assertEqual(sorted_rows.schema.output_names(), ["nim", "nama"])
        self.assertEqual([row.values for row in rows], [(3, "n3"), (2, "n2")])
        self.assertEqual([row.rid for row in rows], [RID("t", 0, 3), RID("t", 0, 2)])
        self.assertTrue(source.closed)


class TestExecutionContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.sm = Mock(spec=StorageManager)
        self.ccm = Mock(spec=ConcurrencyControlManager)

    def test_context_without_transaction_rejects_all_data_authorization(self) -> None:
        ctx = ExecContext(None, self.sm, self.ccm)
        rid = RID("student", 0, 0)
        change = DataChange("student", rid, ChangeOp.INSERT, NO_RECORD, [1, "Ada"])
        for authorize in (
            lambda: ctx.authorize_table("student"),
            lambda: ctx.authorize_rid("student", rid, Action.READ),
            lambda: ctx.authorize_rid("student", rid, Action.WRITE, change),
            lambda: ctx.authorize_insert(change),
        ):
            with self.subTest(authorize=authorize), self.assertRaises(ExecutionError):
                authorize()
        self.assertEqual(self.ccm.mock_calls, [])
        self.assertEqual(self.sm.mock_calls, [])

    def test_old_context_constructor_still_authorizes_a_data_transaction(self) -> None:
        response = AccessResponse(AccessStatus.ALLOWED)
        self.ccm.validate_object.return_value = response
        ctx = ExecContext(7, self.sm, self.ccm, Action.WRITE)

        self.assertIs(ctx.authorize_table("student"), response)
        self.assertEqual(self.ccm.validate_object.call_args.args[0], 7)
        self.assertIs(self.ccm.validate_object.call_args.args[2], Action.READ)

    def test_translator_preserves_schema_owned_by_project(self) -> None:
        self.sm.get_schema.return_value = TableSchema("student", [
            ColumnSchema("id", DataType.INTEGER), ColumnSchema("name", DataType.VARCHAR, length=50),
        ])
        plan = Project(columns=[ColumnRef("student", "name"), ColumnRef("student", "id")],
                       children=[TableScan(table="student")])
        schemas = []

        def project_with_labels(child, columns):
            project = ProjectOp(child, columns)
            project.schema = OutputSchema(project.schema.columns, labels=("nama", "nim"))
            schemas.append(project.schema)
            return project

        with patch("query_processor.translator.ProjectOp", side_effect=project_with_labels):
            executable = PlanTranslator().build(
                ParsedQuery("manual plan", QueryKind.SELECT, plan), ExecContext(7, self.sm, self.ccm)
            )

        self.assertIs(executable._root.schema, schemas[0])
        self.assertEqual(executable._root.schema.output_names(), ["nama", "nim"])
        self.assertEqual([binding.name for binding in schemas[0].columns], ["name", "id"])
        self.assertEqual([call[0] for call in self.sm.mock_calls], ["get_schema"])
        self.assertEqual(self.ccm.mock_calls, [])


if __name__ == "__main__":
    unittest.main()
