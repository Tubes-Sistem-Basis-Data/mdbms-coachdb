from __future__ import annotations

from copy import deepcopy
import unittest
from unittest.mock import create_autospec

from common.catalog import ColumnSchema, DataType, ForeignKey, IndexInfo, TableSchema
from common.errors import SemanticError, StorageError
from common.expression import ColumnRef, Comparison, CompOp, Literal, Subquery
from common.interfaces import ConcurrencyControlManager, StorageManager
from common.plan import (
    CreateTable, DropTable, Filter, Limit, ParsedQuery, Project, QueryKind,
    SubqueryScan, TableScan, TransactionCommand,
)
from query_processor.context import ExecContext
from query_processor.errors import PlanError
from query_processor.operators.ddl import CreateTableExecution, DropTableExecution
from query_processor.translator import PlanTranslator


def table_schema() -> TableSchema:
    return TableSchema(
        "note",
        [
            ColumnSchema("id", DataType.INTEGER, nullable=False),
            ColumnSchema("code", DataType.CHAR, length=8, nullable=False),
            ColumnSchema("score", DataType.FLOAT),
            ColumnSchema("text", DataType.VARCHAR, length=50),
        ],
        primary_key=["id", "code"],
        foreign_keys=[ForeignKey("id", "student", "id")],
        indexes=[IndexInfo("note_code", "note", "code")],
    )


class TestDDLTranslator(unittest.TestCase):
    def setUp(self) -> None:
        self.sm = create_autospec(StorageManager, instance=True, spec_set=True)
        self.ccm = create_autospec(ConcurrencyControlManager, instance=True, spec_set=True)
        self.ctx = ExecContext(tx_id=None, sm=self.sm, ccm=self.ccm)
        self.translator = PlanTranslator()

    def parsed(self, node) -> ParsedQuery:
        kind = QueryKind.CREATE_TABLE if isinstance(node, CreateTable) else QueryKind.DROP_TABLE
        return ParsedQuery("plan DDL manual", kind, node)

    def assert_no_access(self) -> None:
        self.assertEqual(self.sm.mock_calls, [])
        self.assertEqual(self.ccm.mock_calls, [])

    def assert_invalid(self, node) -> None:
        with self.assertRaises(PlanError):
            self.translator.validate(node)
        with self.assertRaises(PlanError):
            self.translator.build(self.parsed(node), self.ctx)
        self.assert_no_access()

    def test_create_build_preserves_plan_and_does_not_access_components(self) -> None:
        node = CreateTable(schema=table_schema())
        before = deepcopy(node)

        self.translator.validate(node)
        executable = self.translator.build(self.parsed(node), self.ctx)

        self.assertIsInstance(executable, CreateTableExecution)
        self.assertEqual(node, before)
        self.assertIsNone(self.ctx.tx_id)
        self.assertIsNone(self.ctx.scalar_resolver)
        self.assert_no_access()

    def test_create_forwards_complete_schema_only_when_executed(self) -> None:
        schema = table_schema()
        before = deepcopy(schema)
        executable = self.translator.build(self.parsed(CreateTable(schema=schema)), self.ctx)
        self.assert_no_access()

        result = executable.execute(self.ctx)

        self.sm.create_table.assert_called_once_with(schema)
        self.assertIs(self.sm.create_table.call_args.args[0], schema)
        self.assertEqual(schema, before)
        self.assertTrue(result.ok)
        self.assertEqual((result.affected_rows, result.columns, result.rows), (0, [], []))
        self.assertEqual(self.ccm.mock_calls, [])

    def test_drop_preserves_restrict_and_cascade_semantics(self) -> None:
        parent = TableSchema("parent", [ColumnSchema("id", DataType.INTEGER)])
        child = TableSchema("child", [ColumnSchema("id", DataType.INTEGER)],
                            foreign_keys=[ForeignKey("id", "parent", "id")])
        self.sm.list_tables.return_value = ["parent", "child"]
        self.sm.get_schema.side_effect = {"parent": parent, "child": child}.__getitem__
        for cascade in (False, True):
            with self.subTest(cascade=cascade):
                self.sm.reset_mock()
                node = DropTable(table="parent", cascade=cascade)
                before = deepcopy(node)
                executable = self.translator.build(self.parsed(node), self.ctx)
                self.assertIsInstance(executable, DropTableExecution)
                self.assertEqual(node, before)
                self.assert_no_access()
                if cascade:
                    result = executable.execute(self.ctx)
                    self.assertTrue(result.ok)
                    self.assertEqual([call.args[0] for call in self.sm.drop_table.call_args_list],
                                     ["child", "parent"])
                else:
                    with self.assertRaisesRegex(SemanticError, "RESTRICT"):
                        executable.execute(self.ctx)
                    self.sm.drop_table.assert_not_called()
                self.assertEqual(self.ccm.mock_calls, [])

    def test_invalid_create_schema_fails_during_validate_and_build(self) -> None:
        invalid = [None, "note", object()]
        for name in (None, "", "   ", 10):
            schema = table_schema()
            schema.name = name
            invalid.append(schema)
        for columns in (None, [], (), [None], ["id"]):
            schema = table_schema()
            schema.columns = columns
            invalid.append(schema)
        for schema in invalid:
            with self.subTest(schema=schema):
                self.assert_invalid(CreateTable(schema=schema))

    def test_invalid_drop_names_and_cascade_fail_before_access(self) -> None:
        for name in (None, "", "   ", 10):
            with self.subTest(name=name):
                self.assert_invalid(DropTable(table=name))
        for cascade in (None, 0, 1, "CASCADE", [], object()):
            with self.subTest(cascade=cascade):
                self.assert_invalid(DropTable(table="note", cascade=cascade))

    def test_ddl_requires_zero_children_and_a_list(self) -> None:
        for node in (CreateTable(schema=table_schema()), DropTable(table="note")):
            for children in (None, (), [TableScan(table="student")], [node]):
                with self.subTest(node=type(node).__name__, children=type(children).__name__):
                    node.children = children
                    self.assert_invalid(node)

    def test_ddl_kind_must_match_its_root(self) -> None:
        for kind in QueryKind:
            for node in (CreateTable(schema=table_schema()), DropTable(table="note")):
                if kind is self.parsed(node).kind:
                    continue
                with self.subTest(kind=kind, node=type(node).__name__), self.assertRaises(PlanError):
                    self.translator.build(ParsedQuery("", kind, node), self.ctx)
        for kind in (QueryKind.CREATE_TABLE, QueryKind.DROP_TABLE):
            for node in (None, TableScan(table="student")):
                with self.subTest(kind=kind, node=node), self.assertRaises(PlanError):
                    self.translator.build(ParsedQuery("", kind, node), self.ctx)
        self.assert_no_access()

    def test_ddl_cannot_carry_transaction_commands(self) -> None:
        for node in (CreateTable(schema=table_schema()), DropTable(table="note")):
            for command in TransactionCommand:
                parsed = self.parsed(node)
                parsed.command = command
                with self.subTest(command=command), self.assertRaises(PlanError):
                    self.translator.build(parsed, self.ctx)
        self.assert_no_access()

    def test_ddl_build_requires_transactionless_context(self) -> None:
        for node in (CreateTable(schema=table_schema()), DropTable(table="note")):
            for tx_id in (1, 0, -1, True, False):
                self.ctx.tx_id = tx_id
                with self.subTest(tx_id=tx_id), self.assertRaisesRegex(PlanError, "tx_id=None"):
                    self.translator.build(self.parsed(node), self.ctx)
                self.assertEqual(self.ctx.tx_id, tx_id)
        self.assert_no_access()

    def test_ddl_cannot_be_a_row_subtree_or_wrapped_in_select(self) -> None:
        for node in (CreateTable(schema=table_schema()), DropTable(table="note")):
            with self.subTest(node=type(node).__name__):
                with self.assertRaisesRegex(PlanError, "subtree baris"):
                    self.translator.build_rows(node, self.ctx)
                for wrapper in (
                    Project(columns=[ColumnRef("note", "id")], children=[node]),
                    Limit(count=0, children=[node]),
                    SubqueryScan(alias="q", children=[node]),
                ):
                    with self.assertRaises(PlanError):
                        self.translator.build(ParsedQuery("", QueryKind.SELECT, wrapper), self.ctx)
        self.assert_no_access()

    def test_ddl_cannot_be_executed_as_a_scalar_subquery(self) -> None:
        for node in (CreateTable(schema=table_schema()), DropTable(table="note")):
            predicate = Comparison(CompOp.EQ, Literal(1, DataType.INTEGER), Subquery(node))
            plan = Project(columns=[ColumnRef("student", "id")], children=[
                Filter(condition=predicate, children=[TableScan(table="student")]),
            ])
            with self.subTest(node=type(node).__name__), self.assertRaises(PlanError):
                self.translator.build(ParsedQuery("", QueryKind.SELECT, plan), self.ctx)
        self.assert_no_access()

    def test_storage_failure_is_deferred_until_execution_and_propagated(self) -> None:
        error = StorageError("create gagal")
        self.sm.create_table.side_effect = error
        executable = self.translator.build(self.parsed(CreateTable(schema=table_schema())), self.ctx)
        self.assert_no_access()
        with self.assertRaises(StorageError) as caught:
            executable.execute(self.ctx)
        self.assertIs(caught.exception, error)
        self.assertEqual(self.ccm.mock_calls, [])


if __name__ == "__main__":
    unittest.main()
