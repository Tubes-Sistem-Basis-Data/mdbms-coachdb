from __future__ import annotations

from copy import deepcopy
import unittest
from unittest.mock import Mock

from common.catalog import ColumnSchema, DataType, ForeignKey, TableSchema
from common.errors import SemanticError, StorageError, TableNotFoundError
from query_processor.context import ExecContext
from query_processor.errors import PlanError, QueryCancelledError
from query_processor.operators.ddl import CreateTableExecution
from tests.qp.ddl_fakes import FakeDDLSM


class TestCreateTableExecution(unittest.TestCase):
    def setUp(self) -> None:
        self.sm = FakeDDLSM()
        self.ccm = Mock()
        self.ctx = ExecContext(tx_id=None, sm=self.sm, ccm=self.ccm)
        self.schema = TableSchema(
            name="student",
            columns=[ColumnSchema("id", DataType.INTEGER, nullable=False)],
            primary_key=["id"],
        )

    def test_create_updates_catalog_and_returns_ddl_result(self) -> None:
        result = CreateTableExecution(self.schema).execute(self.ctx)

        self.assertEqual(self.sm.list_tables(), ["student"])
        self.assertEqual(self.sm.get_schema("student"), self.schema)
        self.assertEqual(len(self.sm.create_calls), 1)
        self.assertIs(self.sm.create_calls[0], self.schema)
        self.assertTrue(result.ok)
        self.assertEqual(result.affected_rows, 0)
        self.assertEqual(result.columns, [])
        self.assertEqual(result.rows, [])
        self.assertIn("student", result.message)
        self.assertEqual(self.ccm.mock_calls, [])
        self.assertIsNone(self.ctx.tx_id)

    def test_preserves_all_types_lengths_nullability_composite_pk_and_fk(self) -> None:
        self.sm.create_table(self.schema)
        schema = TableSchema(
            name="enrollment",
            columns=[
                ColumnSchema("student_id", DataType.INTEGER, nullable=False),
                ColumnSchema("course_id", DataType.CHAR, length=8, nullable=False),
                ColumnSchema("grade", DataType.FLOAT),
                ColumnSchema("note", DataType.VARCHAR, length=50),
            ],
            primary_key=["student_id", "course_id"],
            foreign_keys=[ForeignKey("student_id", "student", "id")],
        )
        original = deepcopy(schema)

        CreateTableExecution(schema).execute(self.ctx)

        self.assertIs(self.sm.create_calls[-1], schema)
        self.assertEqual(self.sm.get_schema("enrollment"), original)
        self.assertEqual(schema, original)
        self.assertEqual(self.sm.list_tables(), ["enrollment", "student"])

    def test_duplicate_preserves_existing_table(self) -> None:
        executor = CreateTableExecution(self.schema)
        executor.execute(self.ctx)
        with self.assertRaisesRegex(SemanticError, "sudah ada"):
            executor.execute(self.ctx)
        self.assertEqual(self.sm.list_tables(), ["student"])
        self.assertEqual(self.sm.get_schema("student"), self.schema)

    def test_storage_errors_propagate_without_catalog_changes(self) -> None:
        for error in (StorageError("gagal membuat tabel"), SemanticError("skema ditolak")):
            with self.subTest(error=type(error).__name__):
                self.sm.create_error = error
                with self.assertRaises(type(error)) as caught:
                    CreateTableExecution(self.schema).execute(self.ctx)
                self.assertIs(caught.exception, error)
                self.assertEqual(self.sm.list_tables(), [])
                with self.assertRaises(TableNotFoundError):
                    self.sm.get_schema("student")

    def test_invalid_parameters_fail_before_storage_call(self) -> None:
        invalid = [None, "student"]
        for name in (None, "", "   ", 12):
            schema = deepcopy(self.schema)
            schema.name = name
            invalid.append(schema)
        for columns in (None, [], (), ["id"]):
            schema = deepcopy(self.schema)
            schema.columns = columns
            invalid.append(schema)
        for schema in invalid:
            with self.subTest(schema=schema):
                with self.assertRaises(PlanError):
                    CreateTableExecution(schema).execute(self.ctx)
        self.assertEqual(self.sm.create_calls, [])
        self.assertEqual(self.sm.list_tables(), [])

    def test_cancelled_request_does_not_create_table(self) -> None:
        self.ctx.cancel_check = lambda: True
        with self.assertRaises(QueryCancelledError):
            CreateTableExecution(self.schema).execute(self.ctx)
        self.assertEqual(self.sm.create_calls, [])
        self.assertEqual(self.sm.list_tables(), [])

    def test_executor_can_retry_after_storage_failure(self) -> None:
        executor = CreateTableExecution(self.schema)
        self.sm.create_error = StorageError("storage tidak tersedia")
        with self.assertRaises(StorageError):
            executor.execute(self.ctx)
        self.sm.create_error = None
        self.assertTrue(executor.execute(self.ctx).ok)
        self.assertEqual(self.sm.get_schema("student"), self.schema)

    def test_fake_catalog_does_not_share_mutable_schema(self) -> None:
        expected = deepcopy(self.schema)
        CreateTableExecution(self.schema).execute(self.ctx)
        self.schema.columns.clear()
        fetched = self.sm.get_schema("student")
        self.assertEqual(fetched, expected)
        fetched.primary_key.clear()
        self.assertEqual(self.sm.get_schema("student"), expected)


if __name__ == "__main__":
    unittest.main()
