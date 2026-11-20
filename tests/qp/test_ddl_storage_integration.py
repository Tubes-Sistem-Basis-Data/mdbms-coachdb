from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common.catalog import ColumnSchema, DataType, ForeignKey, TableSchema
from common.result import ExecutionResult
from concurrency_control import CCManager
from failure_recovery import FailureRecoveryManager
from failure_recovery.log import codec
from query_optimizer import QueryOptimizerFacade
from query_processor.facade import QueryProcessorFacade
from storage_manager.facade import StorageManager


class TestDDLStorageIntegration(unittest.TestCase):
    def setUp(self) -> None:
        previous_log_path = codec.get_log_path()
        self.addCleanup(codec.set_log_path, previous_log_path)
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.start_runtime()

    def start_runtime(self) -> None:
        self.sm = StorageManager(self.temp.name)
        self.addCleanup(self.sm.close)
        self.frm = FailureRecoveryManager(log_path=str(Path(self.temp.name) / "wal.log"))
        self.addCleanup(self.frm.close)
        self.ccm = CCManager(self.frm)
        self.frm.set_dependencies(self.sm, self.ccm)
        self.sm.set_log_flusher(self.frm.flush_log)
        recovered = self.frm.recover()
        self.assertTrue(recovered.ok, recovered.message)
        self.ccm.initialize(recovered.last_tx_id)
        # Konfigurasi default yang juga dipakai main.py, tanpa injeksi allow_ddl.
        self.qp = QueryProcessorFacade(QueryOptimizerFacade(self.sm), self.sm, self.ccm, self.frm)
        self.addCleanup(self.qp.close)
        self.session = self.qp.open_session()

    def execute_ok(self, sql: str) -> ExecutionResult:
        result = self.qp.execute_query(sql, self.session)
        self.assertTrue(result.ok, result.message)
        self.assertIsNone(self.session.tx_id)
        return result

    def create_table(self, sql: str, table: str) -> None:
        result = self.execute_ok(sql)
        self.assertEqual((result.affected_rows, result.columns, result.rows), (0, [], []))
        self.assertIn(
            table, self.sm.list_tables(),
            "SM mengembalikan sukses tetapi tabel tidak terdaftar; "
            "implementasi create_table SM belum memenuhi kontrak.",
        )

    def test_default_ddl_preserves_schema_and_supports_insert_select(self) -> None:
        with patch.object(self.ccm, "begin_transaction", wraps=self.ccm.begin_transaction) as begin:
            self.create_table("CREATE TABLE parent (id INTEGER PRIMARY KEY);", "parent")
            self.create_table(
                "CREATE TABLE measurement (id INTEGER, code CHAR(8), score FLOAT, note VARCHAR(50), "
                "PRIMARY KEY (id, code), FOREIGN KEY (id) REFERENCES parent(id));",
                "measurement",
            )
        begin.assert_not_called()
        self.assertEqual(self.sm.get_schema("measurement"), TableSchema(
            "measurement",
            [ColumnSchema("id", DataType.INTEGER, nullable=False),
             ColumnSchema("code", DataType.CHAR, length=8, nullable=False),
             ColumnSchema("score", DataType.FLOAT),
             ColumnSchema("note", DataType.VARCHAR, length=50)],
            primary_key=["id", "code"],
            foreign_keys=[ForeignKey("id", "parent", "id")],
        ))
        self.execute_ok("INSERT INTO parent VALUES (1);")
        self.execute_ok("INSERT INTO measurement VALUES (1, 'A', 3.5, 'sample');")
        self.assertEqual(self.execute_ok("SELECT id, score, note FROM measurement;").rows,
                         [[1, 3.5, "sample"]])

    def test_create_and_committed_data_survive_restart(self) -> None:
        self.create_table("CREATE TABLE note (id INTEGER PRIMARY KEY, text VARCHAR(30));", "note")
        schema = self.sm.get_schema("note")
        self.execute_ok("INSERT INTO note VALUES (1, 'saved');")
        self.qp.close()
        self.start_runtime()
        self.assertEqual(self.sm.get_schema("note"), schema)
        self.assertEqual(self.execute_ok("SELECT id, text FROM note;").rows, [[1, "saved"]])

    def test_drop_removes_data_and_indexes_and_allows_recreate_after_restart(self) -> None:
        self.create_table("CREATE TABLE note (id INTEGER PRIMARY KEY);", "note")
        self.execute_ok("INSERT INTO note VALUES (1);")
        self.execute_ok("CREATE INDEX ix_note ON note(id);")
        self.assertEqual(len(self.sm.list_indexes("note")), 1)
        self.execute_ok("DROP TABLE note;")
        self.assertNotIn("note", self.sm.list_tables())
        self.assertEqual(self.sm.list_indexes(), [])
        self.qp.close()
        self.start_runtime()
        self.assertNotIn("note", self.sm.list_tables())
        self.create_table("CREATE TABLE note (id INTEGER PRIMARY KEY);", "note")
        self.assertEqual(self.execute_ok("SELECT * FROM note;").rows, [])
        self.execute_ok("CREATE INDEX ix_note ON note(id);")
        self.execute_ok("INSERT INTO note VALUES (2);")
        self.assertEqual(self.execute_ok("SELECT id FROM note WHERE id = 2;").rows, [[2]])

    def test_restrict_preserves_data_and_cascade_drops_transitive_dependents(self) -> None:
        for table, sql in (
            ("parent", "CREATE TABLE parent (id INTEGER PRIMARY KEY);"),
            ("child", "CREATE TABLE child (id INTEGER PRIMARY KEY, pid INTEGER REFERENCES parent(id));"),
            ("leaf", "CREATE TABLE leaf (id INTEGER PRIMARY KEY, cid INTEGER REFERENCES child(id));"),
            ("unrelated", "CREATE TABLE unrelated (id INTEGER);"),
        ):
            self.create_table(sql, table)
        self.execute_ok("INSERT INTO parent VALUES (1);")
        self.execute_ok("INSERT INTO child VALUES (10, 1);")
        self.execute_ok("INSERT INTO leaf VALUES (20, 10);")
        before = {table: self.sm.get_schema(table) for table in self.sm.list_tables()}
        for suffix in ("", " RESTRICT"):
            result = self.qp.execute_query(f"DROP TABLE parent{suffix};", self.session)
            self.assertFalse(result.ok)
            self.assertIn("RESTRICT", result.message)
            self.assertEqual({table: self.sm.get_schema(table) for table in self.sm.list_tables()}, before)
            self.assertEqual(self.execute_ok("SELECT * FROM leaf;").rows, [[20, 10]])
        self.execute_ok("DROP TABLE parent CASCADE;")
        self.assertEqual(self.sm.list_tables(), ["unrelated"])
        self.qp.close()
        self.start_runtime()
        self.assertEqual(self.sm.list_tables(), ["unrelated"])

    def test_active_transaction_rejects_ddl_without_aborting_transaction(self) -> None:
        self.create_table("CREATE TABLE existing (id INTEGER);", "existing")
        begin = self.qp.execute_query("BEGIN;", self.session)
        self.assertTrue(begin.ok, begin.message)
        tx_id = self.session.tx_id
        for sql in ("CREATE TABLE note (id INTEGER);", "DROP TABLE existing;"):
            with self.subTest(sql=sql):
                result = self.qp.execute_query(sql, self.session)
                self.assertFalse(result.ok)
                self.assertIn("transaksi aktif", result.message)
                self.assertEqual(self.session.tx_id, tx_id)
                self.assertTrue(self.session.in_explicit_tx)
                self.assertEqual(self.sm.list_tables(), ["existing"])
        self.execute_ok("ABORT;")
        self.execute_ok("DROP TABLE existing;")
        self.assertEqual(self.sm.list_tables(), [])

    def test_invalid_ddl_preserves_existing_schema_and_session_remains_usable(self) -> None:
        self.create_table("CREATE TABLE existing (id INTEGER PRIMARY KEY);", "existing")
        schema = self.sm.get_schema("existing")
        for sql in (
            "CREATE TABLE existing (id FLOAT);",
            "CREATE TABLE note (id INTEGER, id FLOAT);",
            "CREATE TABLE note (id INTEGER REFERENCES absent(id));",
            "DROP TABLE absent CASCADE;",
        ):
            with self.subTest(sql=sql):
                result = self.qp.execute_query(sql, self.session)
                self.assertFalse(result.ok)
                self.assertIsNone(self.session.tx_id)
                self.assertEqual(self.sm.list_tables(), ["existing"])
                self.assertEqual(self.sm.get_schema("existing"), schema)
        self.execute_ok("INSERT INTO existing VALUES (1);")
        self.assertEqual(self.execute_ok("SELECT * FROM existing;").rows, [[1]])


if __name__ == "__main__":
    unittest.main()
