from __future__ import annotations

from copy import deepcopy
import unittest
from unittest.mock import Mock

from common.catalog import ColumnSchema, DataType, ForeignKey, TableSchema
from common.errors import SemanticError, StorageError, TableNotFoundError
from query_processor.context import ExecContext
from query_processor.errors import PlanError, QueryCancelledError
from query_processor.operators.ddl import CreateTableExecution, DropTableExecution
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


class TestDropTableExecution(unittest.TestCase):
    def setUp(self) -> None:
        self.sm = FakeDDLSM()
        self.ccm = Mock()
        self.ctx = ExecContext(tx_id=None, sm=self.sm, ccm=self.ccm)

    def seed(self, references: dict[str, list[str]]) -> None:
        for table, parents in references.items():
            self.sm.create_table(TableSchema(
                name=table,
                columns=[ColumnSchema("id", DataType.INTEGER, nullable=False)] + [
                    ColumnSchema(f"fk_{i}", DataType.INTEGER) for i in range(len(parents))
                ],
                primary_key=["id"],
                foreign_keys=[ForeignKey(f"fk_{i}", parent, "id")
                              for i, parent in enumerate(parents)],
            ))

    def test_restrict_deletes_only_target_and_returns_ddl_result(self) -> None:
        self.seed({"student": [], "other": []})
        result = DropTableExecution("student").execute(self.ctx)
        self.assertEqual(self.sm.drop_calls, ["student"])
        self.assertEqual(self.sm.list_tables(), ["other"])
        with self.assertRaises(TableNotFoundError):
            self.sm.get_schema("student")
        self.assertTrue(result.ok)
        self.assertEqual(result.affected_rows, 0)
        self.assertEqual(result.columns, [])
        self.assertEqual(result.rows, [])
        self.assertIn("student", result.message)
        self.assertEqual(self.ccm.mock_calls, [])
        self.assertIsNone(self.ctx.tx_id)

    def test_restrict_refuses_other_referrers_before_mutation(self) -> None:
        self.seed({"student": ["student"], "enrollment": ["student"]})
        before = {name: self.sm.get_schema(name) for name in self.sm.list_tables()}
        with self.assertRaisesRegex(SemanticError, "enrollment"):
            DropTableExecution("student", cascade=False).execute(self.ctx)
        self.assertEqual(self.sm.drop_calls, [])
        self.assertEqual(before, {name: self.sm.get_schema(name) for name in self.sm.list_tables()})

    def test_self_reference_is_allowed_in_both_modes(self) -> None:
        for cascade in (False, True):
            with self.subTest(cascade=cascade):
                self.setUp()
                self.seed({"employee": ["employee"]})
                DropTableExecution("employee", cascade).execute(self.ctx)
                self.assertEqual(self.sm.drop_calls, ["employee"])
                self.assertEqual(self.sm.list_tables(), [])

    def test_cascade_chain_keeps_referenced_parent_and_unrelated_table(self) -> None:
        self.seed({"student": [], "course": [], "enrollment": ["student", "course"],
                   "payment": ["enrollment"], "other": []})
        DropTableExecution("student", True).execute(self.ctx)
        self.assertEqual(self.sm.drop_calls, ["payment", "enrollment", "student"])
        self.assertEqual(self.sm.list_tables(), ["course", "other"])

    def test_cascade_diamond_and_duplicate_foreign_keys_have_stable_order(self) -> None:
        references = {"a": [], "b": ["a", "a"], "c": ["a"], "d": ["b", "c"]}
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                self.setUp()
                self.seed(references)
                names = list(references)
                self.sm.list_tables = Mock(return_value=names[::-1] if reverse else names)
                # Return the actual schema objects to detect executor mutation.
                schemas = {name: self.sm.get_schema(name) for name in names}
                before = deepcopy(schemas)
                self.sm.get_schema = Mock(side_effect=schemas.__getitem__)
                DropTableExecution("a", True).execute(self.ctx)
                self.assertEqual(self.sm.drop_calls, ["d", "b", "c", "a"])
                self.assertEqual(schemas, before)
                self.assertEqual(FakeDDLSM.list_tables(self.sm), [])

    def test_cascade_cycle_and_branch_drop_each_member_once(self) -> None:
        self.seed({"a": ["c"], "b": ["a"], "c": ["b"], "d": ["b"], "other": []})
        DropTableExecution("a", True).execute(self.ctx)
        self.assertEqual(self.sm.drop_calls, ["c", "d", "b", "a"])
        self.assertEqual(self.sm.list_tables(), ["other"])

    def test_deep_chain_does_not_require_python_recursion(self) -> None:
        self.seed({f"t{i}": [f"t{i - 1}"] if i else [] for i in range(1100)})
        DropTableExecution("t0", True).execute(self.ctx)
        self.assertEqual(self.sm.drop_calls, [f"t{i}" for i in reversed(range(1100))])
        self.assertEqual(self.sm.list_tables(), [])

    def test_invalid_parameters_do_not_access_storage(self) -> None:
        self.ctx.sm = Mock()
        for table, cascade in [(None, False), ("", False), ("  ", False), (7, False),
                               ("a", None), ("a", 0), ("a", 1), ("a", "CASCADE")]:
            with self.subTest(table=table, cascade=cascade):
                with self.assertRaises(PlanError):
                    DropTableExecution(table, cascade).execute(self.ctx)
        self.assertEqual(self.ctx.sm.mock_calls, [])

    def test_missing_target_does_not_drop_any_table(self) -> None:
        self.seed({"other": []})
        for cascade in (False, True):
            with self.subTest(cascade=cascade):
                with self.assertRaises(TableNotFoundError):
                    DropTableExecution("missing", cascade).execute(self.ctx)
        self.assertEqual(self.sm.drop_calls, [])
        self.assertEqual(self.sm.list_tables(), ["other"])

    def test_catalog_read_failure_finishes_without_mutation(self) -> None:
        self.seed({"a": [], "b": ["a"], "z": []})
        error = StorageError("katalog gagal dibaca")
        for stage in ("list", "schema"):
            with self.subTest(stage=stage):
                self.sm.list_error = error if stage == "list" else None
                self.sm.schema_errors = {"z": error} if stage == "schema" else {}
                with self.assertRaises(StorageError) as caught:
                    DropTableExecution("a", True).execute(self.ctx)
                self.assertIs(caught.exception, error)
                self.assertEqual(self.sm.drop_calls, [])
        self.sm.schema_errors.clear()
        self.assertEqual(self.sm.list_tables(), ["a", "b", "z"])

    def test_dangling_foreign_key_fails_preflight(self) -> None:
        self.seed({"a": [], "b": ["missing"]})
        with self.assertRaisesRegex(SemanticError, "missing"):
            DropTableExecution("a", True).execute(self.ctx)
        self.assertEqual(self.sm.drop_calls, [])
        self.assertEqual(self.sm.list_tables(), ["a", "b"])

    def test_storage_failure_stops_sequence_without_claiming_rollback(self) -> None:
        self.seed({"a": [], "b": ["a"], "c": ["b"]})
        error = StorageError("drop b gagal")
        self.sm.drop_errors["b"] = error
        with self.assertRaises(StorageError) as caught:
            DropTableExecution("a", True).execute(self.ctx)
        self.assertIs(caught.exception, error)
        self.assertEqual(self.sm.drop_calls, ["c", "b"])
        self.assertEqual(self.sm.list_tables(), ["a", "b"])
        with self.assertRaises(TableNotFoundError):
            self.sm.get_schema("c")

    def test_cancellation_before_or_during_mutation_stops_work(self) -> None:
        self.seed({"a": [], "b": ["a"]})
        self.ctx.cancel_check = lambda: True
        with self.assertRaises(QueryCancelledError):
            DropTableExecution("a", True).execute(self.ctx)
        self.assertEqual(self.sm.drop_calls, [])
        self.ctx.cancel_check = lambda: bool(self.sm.drop_calls)
        with self.assertRaises(QueryCancelledError):
            DropTableExecution("a", True).execute(self.ctx)
        self.assertEqual(self.sm.drop_calls, ["b"])
        self.assertEqual(self.sm.list_tables(), ["a"])

    def test_reuse_rebuilds_graph_after_preflight_failure(self) -> None:
        self.seed({"a": [], "b": ["a"]})
        executor = DropTableExecution("a")
        with self.assertRaises(SemanticError):
            executor.execute(self.ctx)
        self.sm.drop_table("b")
        self.assertTrue(executor.execute(self.ctx).ok)
        self.assertEqual(self.sm.list_tables(), [])
        with self.assertRaises(TableNotFoundError):
            executor.execute(self.ctx)
        self.assertEqual(self.sm.drop_calls, ["b", "a"])

    def test_fake_drop_missing_table_preserves_catalog(self) -> None:
        self.seed({"a": []})
        with self.assertRaises(TableNotFoundError):
            self.sm.drop_table("missing")
        self.assertEqual(self.sm.list_tables(), ["a"])


if __name__ == "__main__":
    unittest.main()
