from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterator
import unittest
from unittest.mock import patch

from common.catalog import ColumnSchema, DataType, IndexInfo, IndexKind, TableSchema
from common.interfaces import Session
from common.result import ExecutionResult
from concurrency_control import CCManager
from failure_recovery import FailureRecoveryManager
from failure_recovery.log import codec
from query_optimizer import QueryOptimizerFacade
from query_processor.facade import QueryProcessorFacade
from storage_manager.facade import StorageManager


# Banyak halaman dan key selektif supaya QO memilih index berdasarkan biaya asli.
ROWS = [[i, i // 2, "x" * 160] for i in range(512)]


class IndexRuntime:
    def __init__(self, directory: str) -> None:
        self.sm = StorageManager(directory)
        self.frm = FailureRecoveryManager(log_path=str(Path(directory) / "wal.log"))
        self.ccm = CCManager(self.frm)
        self.frm.set_dependencies(self.sm, self.ccm)
        self.sm.set_log_flusher(self.frm.flush_log)
        recovery = self.frm.recover()
        if not recovery.ok:
            self.sm.close()
            self.frm.close()
            raise AssertionError(recovery.message)
        self.ccm.initialize(recovery.last_tx_id)
        self.qp = QueryProcessorFacade(QueryOptimizerFacade(self.sm), self.sm, self.ccm, self.frm)
        self.session: Session = self.qp.open_session()

    def close(self) -> None:
        self.qp.close()


@contextmanager
def runtime(rows: list[list] = ROWS) -> Iterator[tuple[IndexRuntime, str]]:
    previous_log_path = codec.get_log_path()
    try:
        with TemporaryDirectory() as directory:
            seed = StorageManager(directory)
            try:
                seed.create_table(TableSchema(
                    "items",
                    [ColumnSchema("id", DataType.INTEGER, nullable=False),
                     ColumnSchema("search_key", DataType.INTEGER),
                     ColumnSchema("payload", DataType.VARCHAR, length=180)],
                    primary_key=["id"],
                ))
                if "items" not in seed.list_tables():
                    raise AssertionError(
                        "Prasyarat integrasi belum terpenuhi: "
                        "StorageManager.create_table() belum mendaftarkan tabel 'items'. "
                        "Implementasi API SM diperlukan sebelum test index dapat berjalan."
                    )
                for values in rows:
                    rid = seed.allocate_rid("items")
                    seed.insert("items", rid, values, None)
            finally:
                seed.close()
            app = IndexRuntime(directory)
            try:
                yield app, directory
            finally:
                app.close()
    finally:
        codec.set_log_path(previous_log_path)


class TestIndexIntegration(unittest.TestCase):
    def execute_ok(self, app: IndexRuntime, sql: str) -> ExecutionResult:
        result = app.qp.execute_query(sql, app.session)
        self.assertTrue(result.ok, result.message)
        self.assertIsNone(app.session.tx_id)
        return result

    def create_index(self, app: IndexRuntime, kind: IndexKind) -> None:
        using = "HASH" if kind is IndexKind.HASH else "BTREE"
        result = self.execute_ok(
            app, f"CREATE INDEX ix_key ON items(search_key) USING {using};",
        )
        self.assertEqual(result.affected_rows, 0)
        expected = IndexInfo("ix_key", "items", "search_key", kind)
        self.assertEqual(app.sm.list_indexes("items"), [expected])
        self.assertEqual(app.sm.get_schema("items").indexes, [expected])

    def assert_index_query(
        self, app: IndexRuntime, predicate: str, expected: list[list],
        method: str, bounds: tuple,
    ) -> None:
        sql = f"SELECT id, search_key FROM items WHERE {predicate} ORDER BY id;"
        plan = self.execute_ok(app, "EXPLAIN " + sql)
        self.assertIn("IndexScan", str(plan.rows) + plan.message)
        # Spies menjalankan method SM asli
        with patch.object(app.sm, method, wraps=getattr(app.sm, method)) as lookup, \
             patch.object(app.sm, "fetch", wraps=app.sm.fetch) as fetch, \
             patch.object(app.sm, "scan", side_effect=AssertionError("SELECT memakai TableScan")):
            result = self.execute_ok(app, sql)
        lookup.assert_called_once_with("items", "search_key", *bounds)
        self.assertEqual(result.columns, ["id", "search_key"])
        self.assertEqual(result.rows, expected)
        self.assertGreaterEqual(fetch.call_count, len(expected))
        for args in fetch.call_args_list:
            self.assertEqual(args.args[0], "items")
            self.assertEqual(args.args[1].table, "items")

    def test_create_and_equality_use_real_indexes_with_duplicate_keys(self) -> None:
        for kind in IndexKind:
            with self.subTest(kind=kind), runtime() as (app, _):
                baseline = self.execute_ok(
                    app, "SELECT id, search_key FROM items WHERE search_key = 100 ORDER BY id;",
                ).rows
                self.assertEqual(baseline, [[200, 100], [201, 100]])
                self.create_index(app, kind)
                self.assert_index_query(app, "search_key = 100", baseline, "index_lookup", (100,))
                rids = app.sm.index_lookup("items", "search_key", 100)
                self.assertEqual(len(set(rids)), 2)
                self.assertEqual([app.sm.fetch("items", rid).values[:2] for rid in rids], baseline)

    def test_missing_key_returns_empty_through_real_index(self) -> None:
        for kind in IndexKind:
            with self.subTest(kind=kind), runtime() as (app, _):
                self.create_index(app, kind)
                self.assert_index_query(app, "search_key = 999", [], "index_lookup", (999,))

    def test_index_on_empty_table_is_populated_by_subsequent_sql_insert(self) -> None:
        for kind in IndexKind:
            with self.subTest(kind=kind), runtime([]) as (app, _):
                self.create_index(app, kind)
                self.assertEqual(app.sm.index_lookup("items", "search_key", 100), [])
                self.assertEqual(self.execute_ok(
                    app, "SELECT id FROM items WHERE search_key = 100;",
                ).rows, [])
                self.execute_ok(app, "INSERT INTO items VALUES (1, 100, 'new');")
                self.assertEqual(self.execute_ok(
                    app, "SELECT id FROM items WHERE search_key = 100;",
                ).rows, [[1]])
                rids = app.sm.index_lookup("items", "search_key", 100)
                self.assertEqual(len(rids), 1)
                self.assertEqual(app.sm.fetch("items", rids[0]).values, [1, 100, "new"])

    def test_sql_writes_and_abort_keep_real_index_consistent(self) -> None:
        for kind in IndexKind:
            with self.subTest(kind=kind), runtime() as (app, _):
                self.create_index(app, kind)
                self.execute_ok(app, "INSERT INTO items VALUES (1000, 100, 'new');")
                self.assert_index_query(
                    app, "search_key = 100", [[200, 100], [201, 100], [1000, 100]],
                    "index_lookup", (100,),
                )
                self.execute_ok(app, "UPDATE items SET search_key = 101 WHERE id = 1000;")
                self.assert_index_query(
                    app, "search_key = 100", [[200, 100], [201, 100]], "index_lookup", (100,),
                )
                self.assert_index_query(
                    app, "search_key = 101", [[202, 101], [203, 101], [1000, 101]],
                    "index_lookup", (101,),
                )
                # Transaksi eksplisit memakai jalur CCM/FRM dan undo SM asli.
                begin = app.qp.execute_query("BEGIN;", app.session)
                self.assertTrue(begin.ok, begin.message)
                changed = app.qp.execute_query(
                    "UPDATE items SET search_key = 102 WHERE id = 1000;", app.session,
                )
                self.assertTrue(changed.ok, changed.message)
                self.execute_ok(app, "ABORT;")
                self.assert_index_query(
                    app, "search_key = 101", [[202, 101], [203, 101], [1000, 101]],
                    "index_lookup", (101,),
                )
                self.assert_index_query(
                    app, "search_key = 102", [[204, 102], [205, 102]], "index_lookup", (102,),
                )
                self.execute_ok(app, "DELETE FROM items WHERE id = 1000;")
                self.assert_index_query(
                    app, "search_key = 101", [[202, 101], [203, 101]], "index_lookup", (101,),
                )

    def test_btree_ranges_match_table_scan_at_strict_and_inclusive_boundaries(self) -> None:
        cases = (
            ("search_key > 253", (253, None), lambda key: key > 253),
            ("search_key >= 253", (253, None), lambda key: key >= 253),
            ("search_key < 2", (None, 2), lambda key: key < 2),
            ("search_key <= 2", (None, 2), lambda key: key <= 2),
            ("search_key >= 100 AND search_key <= 102", (100, 102), lambda key: 100 <= key <= 102),
            ("search_key > 100 AND search_key < 102", (100, 102), lambda key: 100 < key < 102),
        )
        with runtime() as (app, _):
            baseline = {
                predicate: self.execute_ok(
                    app, f"SELECT id, search_key FROM items WHERE {predicate} ORDER BY id;",
                ).rows
                for predicate, _, _ in cases
            }
            self.create_index(app, IndexKind.BPLUS_TREE)
            for predicate, bounds, matches in cases:
                with self.subTest(predicate=predicate):
                    expected = [row[:2] for row in ROWS if matches(row[1])]
                    self.assertEqual(baseline[predicate], expected)
                    self.assert_index_query(app, predicate, expected, "index_range", bounds)

    def test_residual_filter_is_applied_after_index_lookup(self) -> None:
        with runtime() as (app, _):
            self.create_index(app, IndexKind.BPLUS_TREE)
            self.assert_index_query(
                app, "search_key = 100 AND id > 200", [[201, 100]], "index_lookup", (100,),
            )

    def test_hash_range_uses_table_scan_instead_of_unsupported_index_range(self) -> None:
        with runtime() as (app, _):
            self.create_index(app, IndexKind.HASH)
            with patch.object(app.sm, "index_range", side_effect=AssertionError("HASH range")), \
                 patch.object(app.sm, "scan", wraps=app.sm.scan) as scan:
                result = self.execute_ok(
                    app, "SELECT id, search_key FROM items WHERE search_key > 253 ORDER BY id;",
                )
            self.assertEqual(result.rows, [row[:2] for row in ROWS if row[1] > 253])
            scan.assert_called_once_with("items")

    def test_invalid_create_does_not_change_index_metadata(self) -> None:
        with runtime() as (app, _):
            self.create_index(app, IndexKind.BPLUS_TREE)
            before = app.sm.list_indexes()
            for sql in (
                "CREATE INDEX ix_key ON items(id);",
                "CREATE INDEX ix_other ON items(search_key);",
                "CREATE INDEX ix_missing ON items(missing);",
                "CREATE INDEX ix_absent ON absent(id);",
            ):
                with self.subTest(sql=sql):
                    result = app.qp.execute_query(sql, app.session)
                    self.assertFalse(result.ok)
                    self.assertIsNone(app.session.tx_id)
                    self.assertEqual(app.sm.list_indexes(), before)
            self.assert_index_query(
                app, "search_key = 100", [[200, 100], [201, 100]], "index_lookup", (100,),
            )

    def test_index_metadata_and_queries_survive_runtime_restart(self) -> None:
        for kind in IndexKind:
            with self.subTest(kind=kind), runtime() as (app, directory):
                self.create_index(app, kind)
                self.execute_ok(app, "INSERT INTO items VALUES (1000, 100, 'committed');")
                app.close()
                restarted = IndexRuntime(directory)
                try:
                    self.assertEqual(restarted.sm.list_indexes("items"), [
                        IndexInfo("ix_key", "items", "search_key", kind),
                    ])
                    self.assert_index_query(
                        restarted, "search_key = 100", [[200, 100], [201, 100], [1000, 100]],
                        "index_lookup", (100,),
                    )
                    if kind is IndexKind.BPLUS_TREE:
                        self.assert_index_query(
                            restarted, "search_key > 253", [row[:2] for row in ROWS if row[1] > 253],
                            "index_range", (253, None),
                        )
                finally:
                    restarted.close()


if __name__ == "__main__":
    unittest.main()
