"""SQL DDL dengan QO/QP asli dan katalog fake; bukan uji persistensi storage."""

from __future__ import annotations

from copy import deepcopy
import unittest
from unittest.mock import Mock

from common.catalog import ColumnSchema, DataType, ForeignKey, TableSchema
from common.errors import StorageError
from common.interfaces import ConcurrencyControlManager
from common.transaction import AccessResponse, AccessStatus, TxResult, TxStatus
from query_optimizer import QueryOptimizerFacade
from query_processor.cancellation import cancellation_scope
from query_processor.ddl import DDLCoordinator
from query_processor.facade import QueryProcessorFacade
from tests.qp.ddl_fakes import FakeDDLSM
from tests.qp.test_bonus_facade import CatalogMutatingFakeSM


class TestDDLPlanIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self.sm = FakeDDLSM()
        self.ccm = Mock(spec=ConcurrencyControlManager)
        self.ccm.begin_transaction.return_value = 7
        self.ccm.commit.side_effect = lambda tx: TxResult(tx, TxStatus.COMMITTED, True)
        self.ccm.abort.side_effect = lambda tx: TxResult(tx, TxStatus.ABORTED, True)
        self.qo = QueryOptimizerFacade(self.sm)
        # Aktif hanya pada fake katalog; aplikasi tetap memakai default allow_ddl=False.
        self.qp = QueryProcessorFacade(
            qo=self.qo, sm=self.sm, ccm=self.ccm, ddl=DDLCoordinator(allow_ddl=True),
        )
        self.session = self.qp.open_session()

    def tearDown(self) -> None:
        self.qp.close_session(self.session.session_id)

    def execute_ok(self, sql: str):
        result = self.qp.execute_query(sql, self.session)
        self.assertTrue(result.ok, result.message)
        self.assertEqual((result.affected_rows, result.columns, result.rows), (0, [], []))
        self.assertIsNone(self.session.tx_id)
        return result

    def test_create_preserves_bound_types_lengths_composite_pk_and_fk(self) -> None:
        self.execute_ok("CREATE TABLE parent (id INTEGER PRIMARY KEY);")
        self.execute_ok(
            "CREATE TABLE measurement (id INTEGER, code CHAR(8), score FLOAT, note VARCHAR(50), "
            "PRIMARY KEY (id, code), FOREIGN KEY (id) REFERENCES parent(id));"
        )
        expected = TableSchema(
            "measurement",
            [ColumnSchema("id", DataType.INTEGER, nullable=False),
             ColumnSchema("code", DataType.CHAR, length=8, nullable=False),
             ColumnSchema("score", DataType.FLOAT),
             ColumnSchema("note", DataType.VARCHAR, length=50)],
            primary_key=["id", "code"],
            foreign_keys=[ForeignKey("id", "parent", "id")],
        )
        self.assertEqual(self.sm.get_schema("measurement"), expected)
        self.assertEqual(self.sm.create_calls[-1], expected)
        self.assertEqual(self.ccm.mock_calls, [])

    def test_restrict_and_cascade_use_actual_executors_for_a_diamond(self) -> None:
        for sql in (
            "CREATE TABLE root_node (id INTEGER PRIMARY KEY);",
            "CREATE TABLE left_node (id INTEGER PRIMARY KEY, root_id INTEGER REFERENCES root_node(id));",
            "CREATE TABLE right_node (id INTEGER PRIMARY KEY, root_id INTEGER REFERENCES root_node(id));",
            "CREATE TABLE leaf_node (id INTEGER PRIMARY KEY, l INTEGER REFERENCES left_node(id), "
            "r INTEGER REFERENCES right_node(id));",
            "CREATE TABLE unrelated (id INTEGER);",
        ):
            self.execute_ok(sql)
        before = {name: self.sm.get_schema(name) for name in self.sm.list_tables()}

        result = self.qp.execute_query("DROP TABLE root_node;", self.session)
        self.assertFalse(result.ok)
        self.assertIn("RESTRICT", result.message)
        self.assertEqual(self.sm.drop_calls, [])
        self.assertEqual({name: self.sm.get_schema(name) for name in self.sm.list_tables()}, before)

        self.execute_ok("DROP TABLE root_node CASCADE;")
        self.assertEqual(self.sm.drop_calls, ["leaf_node", "left_node", "right_node", "root_node"])
        self.assertEqual(self.sm.list_tables(), ["unrelated"])
        self.assertEqual(self.ccm.mock_calls, [])

    def test_self_reference_can_be_dropped_with_restrict(self) -> None:
        self.execute_ok("CREATE TABLE node (id INTEGER PRIMARY KEY, parent INTEGER REFERENCES node(id));")
        self.execute_ok("DROP TABLE node RESTRICT;")
        self.assertEqual(self.sm.list_tables(), [])
        self.assertEqual(self.sm.drop_calls, ["node"])
        self.assertEqual(self.ccm.mock_calls, [])

    def test_default_coordinator_rejects_ddl_before_mutation_or_data_transaction(self) -> None:
        self.execute_ok("CREATE TABLE existing (id INTEGER);")
        qp = QueryProcessorFacade(qo=self.qo, sm=self.sm, ccm=self.ccm)
        session = qp.open_session()
        try:
            for sql in ("CREATE TABLE note (id INTEGER);", "DROP TABLE existing;"):
                with self.subTest(sql=sql):
                    result = qp.execute_query(sql, session)
                    self.assertFalse(result.ok)
                    self.assertIn("DDL", result.message)
                    self.assertIsNone(session.tx_id)
            self.assertEqual(self.sm.list_tables(), ["existing"])
            self.assertEqual(len(self.sm.create_calls), 1)
            self.assertEqual(self.sm.drop_calls, [])
            self.assertEqual(self.ccm.mock_calls, [])
        finally:
            qp.close_session(session.session_id)

    def test_active_transaction_rejects_create_and_drop_without_aborting_it(self) -> None:
        self.execute_ok("CREATE TABLE existing (id INTEGER);")
        self.assertTrue(self.qp.execute_query("BEGIN;", self.session).ok)
        for sql in ("CREATE TABLE note (id INTEGER);", "DROP TABLE existing CASCADE;"):
            with self.subTest(sql=sql):
                result = self.qp.execute_query(sql, self.session)
                self.assertFalse(result.ok)
                self.assertIn("transaksi aktif", result.message)
                self.assertEqual(self.session.tx_id, 7)
                self.assertTrue(self.session.in_explicit_tx)
        self.ccm.begin_transaction.assert_called_once()
        self.ccm.commit.assert_not_called()
        self.ccm.abort.assert_not_called()
        self.assertEqual(self.sm.list_tables(), ["existing"])
        self.assertEqual(self.sm.drop_calls, [])
        self.assertEqual(len(self.sm.create_calls), 1)
        self.assertTrue(self.qp.execute_query("ABORT;", self.session).ok)
        self.execute_ok("DROP TABLE existing;")

    def test_invalid_schema_duplicate_and_missing_table_do_not_mutate_catalog(self) -> None:
        self.execute_ok("CREATE TABLE existing (id INTEGER PRIMARY KEY);")
        before = deepcopy(self.sm.get_schema("existing"))
        for sql in (
            "CREATE TABLE existing (id FLOAT);",
            "CREATE TABLE note (id INTEGER, id FLOAT);",
            "CREATE TABLE note (id INTEGER, PRIMARY KEY (missing));",
            "CREATE TABLE note (id INTEGER REFERENCES absent(id));",
            "CREATE TABLE note (id FLOAT REFERENCES existing(id));",
            "DROP TABLE absent CASCADE;",
        ):
            with self.subTest(sql=sql):
                result = self.qp.execute_query(sql, self.session)
                self.assertFalse(result.ok)
                self.assertIsNone(self.session.tx_id)
                self.assertEqual(self.sm.list_tables(), ["existing"])
                self.assertEqual(self.sm.get_schema("existing"), before)
        self.assertEqual(len(self.sm.create_calls), 1)
        self.assertEqual(self.sm.drop_calls, [])
        self.assertEqual(self.ccm.mock_calls, [])

    def test_storage_errors_and_partial_cascade_are_returned_as_failures(self) -> None:
        self.sm.create_error = StorageError("create ditolak SM")
        result = self.qp.execute_query("CREATE TABLE parent (id INTEGER PRIMARY KEY);", self.session)
        self.assertFalse(result.ok)
        self.assertIn("create ditolak SM", result.message)
        self.assertEqual(self.sm.list_tables(), [])
        self.sm.create_error = None
        for sql in (
            "CREATE TABLE parent (id INTEGER PRIMARY KEY);",
            "CREATE TABLE child (id INTEGER PRIMARY KEY, pid INTEGER REFERENCES parent(id));",
            "CREATE TABLE leaf (id INTEGER REFERENCES child(id));",
        ):
            self.execute_ok(sql)
        self.sm.drop_errors["child"] = StorageError("drop child ditolak SM")

        result = self.qp.execute_query("DROP TABLE parent CASCADE;", self.session)

        self.assertFalse(result.ok)
        self.assertIn("drop child ditolak SM", result.message)
        self.assertEqual(self.sm.drop_calls, ["leaf", "child"])
        self.assertEqual(self.sm.list_tables(), ["child", "parent"])
        self.assertIsNone(self.session.tx_id)
        self.assertEqual(self.ccm.mock_calls, [])
        self.sm.drop_errors.clear()
        self.execute_ok("DROP TABLE parent CASCADE;")
        self.assertEqual(self.sm.list_tables(), [])

    def test_cancelled_ddl_does_not_mutate_or_start_a_transaction(self) -> None:
        self.execute_ok("CREATE TABLE existing (id INTEGER);")
        with cancellation_scope(lambda: True):
            for sql in ("CREATE TABLE note (id INTEGER);", "DROP TABLE existing;"):
                with self.subTest(sql=sql):
                    result = self.qp.execute_query(sql, self.session)
                    self.assertFalse(result.ok)
                    self.assertIn("dibatalkan", result.message)
        self.assertEqual(self.sm.list_tables(), ["existing"])
        self.assertEqual(len(self.sm.create_calls), 1)
        self.assertEqual(self.sm.drop_calls, [])
        self.assertEqual(self.ccm.mock_calls, [])

    def test_created_table_supports_dml_and_select_bonus_with_default_translator(self) -> None:
        sm = CatalogMutatingFakeSM()
        self.ccm.validate_object.return_value = AccessResponse(AccessStatus.ALLOWED, lsn=1)
        qp = QueryProcessorFacade(
            qo=QueryOptimizerFacade(sm), sm=sm, ccm=self.ccm,
            ddl=DDLCoordinator(allow_ddl=True),
        )
        session = qp.open_session()
        try:
            for sql in (
                "CREATE TABLE note (id INTEGER PRIMARY KEY, student_id INTEGER REFERENCES student(id));",
                "INSERT INTO note VALUES (10, 1);",
            ):
                result = qp.execute_query(sql, session)
                self.assertTrue(result.ok, result.message)
            result = qp.execute_query(
                "SELECT q.note_id FROM (SELECT n.id AS note_id FROM note n "
                "WHERE n.student_id = (SELECT s.id FROM student s WHERE s.id = 1 LIMIT 1) "
                "LIMIT 1) AS q;", session,
            )
            self.assertTrue(result.ok, result.message)
            self.assertEqual((result.columns, result.rows), (["note_id"], [[10]]))
            self.assertEqual(self.ccm.begin_transaction.call_count, 2)
            self.assertEqual(self.ccm.commit.call_count, 2)
            self.ccm.abort.assert_not_called()
            self.assertTrue(qp.execute_query("DROP TABLE note;", session).ok)
            self.assertNotIn("note", sm.list_tables())
            self.assertEqual(self.ccm.begin_transaction.call_count, 2)
            self.assertEqual(self.ccm.commit.call_count, 2)
        finally:
            qp.close_session(session.session_id)


if __name__ == "__main__":
    unittest.main()
