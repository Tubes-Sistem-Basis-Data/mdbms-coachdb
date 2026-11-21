from __future__ import annotations

from contextlib import contextmanager, redirect_stdout
import io
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Event, Thread
import unittest
from unittest.mock import patch

from common.errors import StorageError
from common.log import CheckpointResult
from failure_recovery.log import codec
from main import bootstrap, close_runtime, parse_args


@contextmanager
def runtime(directory, protocol):
    previous = codec.get_log_path()
    try:
        with redirect_stdout(io.StringIO()):
            qp = bootstrap(parse_args(["--data-dir", directory, "--protocol", protocol]))
        try:
            yield qp
        finally:
            close_runtime(qp)
    finally:
        codec.set_log_path(previous)


CRASH_DURING_DDL = """
import os, sys
from main import bootstrap, parse_args
qp = bootstrap(parse_args(['--data-dir', sys.argv[1], '--protocol', sys.argv[3]]))
session = qp.open_session()
def execute(sql):
    result = qp.execute_query(sql, session)
    assert result.ok, result.message
mode = sys.argv[2]
execute('CREATE TABLE item (id INTEGER PRIMARY KEY);')
execute('INSERT INTO item VALUES (7);')
if mode == 'cascade':
    execute('CREATE TABLE child (id INTEGER REFERENCES item(id));')
    execute('INSERT INTO child VALUES (7);')
if mode == 'create':
    execute('DROP TABLE item;')
method = 'create_table' if mode == 'create' else 'drop_table'
original = getattr(qp._sm, method)
def crash_after_storage(*args):
    original(*args)
    os._exit(73)
setattr(qp._sm, method, crash_after_storage)
execute('CREATE TABLE item (id INTEGER PRIMARY KEY);' if mode == 'create'
        else 'DROP TABLE item CASCADE;')
raise AssertionError('DDL did not reach storage')
"""


class TestDDLSafetyIntegration(unittest.TestCase):
    def execute_ok(self, qp, session, sql):
        result = qp.execute_query(sql, session)
        self.assertTrue(result.ok, result.message)
        return result

    def seed(self, qp, session):
        self.execute_ok(qp, session, "CREATE TABLE item (id INTEGER PRIMARY KEY, value INTEGER);")
        self.execute_ok(qp, session, "INSERT INTO item VALUES (1, 10);")

    def test_drop_recreate_does_not_resurrect_old_rows_after_two_restarts(self) -> None:
        for protocol in ("lock", "timestamp"):
            with self.subTest(protocol=protocol), TemporaryDirectory() as directory:
                with runtime(directory, protocol) as qp:
                    session = qp.open_session()
                    self.seed(qp, session)
                    self.execute_ok(qp, session, "DROP TABLE item;")
                    self.execute_ok(qp, session, "CREATE TABLE item (id INTEGER PRIMARY KEY, value INTEGER);")
                    self.assertEqual(self.execute_ok(qp, session, "SELECT * FROM item;").rows, [])
                for _ in range(2):
                    with runtime(directory, protocol) as qp:
                        session = qp.open_session()
                        self.assertEqual(self.execute_ok(qp, session, "SELECT * FROM item;").rows, [])

    def test_other_clients_transaction_rejects_ddl_and_can_still_abort(self) -> None:
        for protocol in ("lock", "timestamp"):
            with self.subTest(protocol=protocol), TemporaryDirectory() as directory:
                with runtime(directory, protocol) as qp:
                    writer, admin = qp.open_session(), qp.open_session()
                    self.seed(qp, admin)
                    self.execute_ok(qp, writer, "BEGIN;")
                    self.execute_ok(qp, writer, "UPDATE item SET value = 20 WHERE id = 1;")
                    tx_id = writer.tx_id
                    with patch.object(qp._frm, "checkpoint", wraps=qp._frm.checkpoint) as checkpoint:
                        for sql in ("DROP TABLE item;", "CREATE TABLE other (id INTEGER);"):
                            result = qp.execute_query(sql, admin)
                            self.assertFalse(result.ok)
                            self.assertIn("transaksi aktif", result.message)
                            self.assertEqual(writer.tx_id, tx_id)
                        checkpoint.assert_not_called()
                    self.execute_ok(qp, writer, "ABORT;")
                    self.assertEqual(self.execute_ok(qp, admin, "SELECT * FROM item;").rows, [[1, 10]])
                    self.execute_ok(qp, admin, "DROP TABLE item;")

    def test_running_select_rejects_ddl_without_blocking_its_commit(self) -> None:
        for protocol in ("lock", "timestamp"):
            with self.subTest(protocol=protocol), TemporaryDirectory() as directory:
                with runtime(directory, protocol) as qp:
                    reader, admin = qp.open_session(), qp.open_session()
                    self.seed(qp, admin)
                    entered, release = Event(), Event()
                    results = []
                    scan = qp._sm.scan

                    def blocked_scan(table):
                        entered.set()
                        if not release.wait(3):
                            raise AssertionError("scan was not released")
                        return scan(table)

                    with patch.object(qp._sm, "scan", side_effect=blocked_scan):
                        thread = Thread(target=lambda: results.append(
                            qp.execute_query("SELECT * FROM item;", reader)))
                        thread.start()
                        try:
                            self.assertTrue(entered.wait(2))
                            result = qp.execute_query("DROP TABLE item;", admin)
                            self.assertFalse(result.ok)
                            self.assertIn("request lain", result.message)
                        finally:
                            release.set()
                            thread.join(3)
                        self.assertFalse(thread.is_alive())
                    self.assertEqual(len(results), 1)
                    self.assertTrue(results[0].ok, results[0].message)
                    self.assertEqual(results[0].rows, [[1, 10]])
                    self.assertIsNone(reader.tx_id)
                    self.execute_ok(qp, admin, "DROP TABLE item;")

    def test_ddl_blocks_new_requests_and_nested_admin_pause_resume(self) -> None:
        for protocol in ("lock", "timestamp"):
            with self.subTest(protocol=protocol), TemporaryDirectory() as directory:
                with runtime(directory, protocol) as qp:
                    admin, other = qp.open_session(), qp.open_session()
                    self.seed(qp, admin)
                    entered, release = Event(), Event()
                    results = []
                    drop = qp._sm.drop_table

                    def blocked_drop(table):
                        entered.set()
                        if not release.wait(3):
                            raise AssertionError("DDL was not released")
                        return drop(table)

                    with patch.object(qp._sm, "drop_table", side_effect=blocked_drop), \
                         patch.object(qp._ccm, "resume_transactions", wraps=qp._ccm.resume_transactions) as resume:
                        thread = Thread(target=lambda: results.append(qp.execute_query("DROP TABLE item;", admin)))
                        thread.start()
                        try:
                            self.assertTrue(entered.wait(2))
                            calls_before = resume.call_count
                            for sql in ("BEGIN;", "SELECT * FROM item;", "CHECKPOINT;", "RESTORE TO TX 1;",
                                        "CREATE TABLE other (id INTEGER);"):
                                result = qp.execute_query(sql, other)
                                self.assertFalse(result.ok, sql)
                                self.assertIn("administrasi sedang berjalan", result.message)
                                self.assertIsNone(other.tx_id)
                            self.assertEqual(resume.call_count, calls_before)
                            # Checkpoint eksternal/periodik tidak membuka admission QP.
                            self.assertTrue(qp._frm.checkpoint().ok)
                            result = qp.execute_query("BEGIN;", other)
                            self.assertFalse(result.ok)
                            self.assertIsNone(other.tx_id)
                        finally:
                            release.set()
                            thread.join(3)
                        self.assertFalse(thread.is_alive())
                    self.assertEqual(len(results), 1)
                    self.assertTrue(results[0].ok, results[0].message)
                    self.execute_ok(qp, other, "BEGIN;")
                    self.execute_ok(qp, other, "ABORT;")

    def test_checkpoint_failures_do_not_allow_unsafe_ddl_or_false_success(self) -> None:
        for protocol in ("lock", "timestamp"):
            with self.subTest(protocol=protocol), TemporaryDirectory() as directory:
                with runtime(directory, protocol) as qp:
                    session = qp.open_session()
                    self.seed(qp, session)
                    failed = CheckpointResult(ok=False, message="snapshot gagal")
                    with patch.object(qp._frm, "checkpoint", return_value=failed), \
                         patch.object(qp._sm, "drop_table", wraps=qp._sm.drop_table) as drop:
                        result = qp.execute_query("DROP TABLE item;", session)
                    self.assertFalse(result.ok)
                    self.assertIn("sebelum DDL", result.message)
                    drop.assert_not_called()
                    self.assertEqual(self.execute_ok(qp, session, "SELECT * FROM item;").rows, [[1, 10]])

                    checkpoint = qp._frm.checkpoint
                    calls = []

                    def fail_after_drop():
                        calls.append(True)
                        return checkpoint() if len(calls) == 1 else failed

                    with patch.object(qp._frm, "checkpoint", side_effect=fail_after_drop):
                        result = qp.execute_query("DROP TABLE item;", session)
                    self.assertFalse(result.ok)
                    self.assertIn("setelah DDL", result.message)
                    self.assertNotIn("item", qp._sm.list_tables())
                # Batas redo sebelum DDL tetap aman meski checkpoint setelahnya gagal.
                with runtime(directory, protocol) as qp:
                    self.assertEqual(qp._sm.list_tables(), [])

    def test_partial_cascade_failure_is_reported_and_remains_recoverable(self) -> None:
        for protocol in ("lock", "timestamp"):
            with self.subTest(protocol=protocol), TemporaryDirectory() as directory:
                with runtime(directory, protocol) as qp:
                    session = qp.open_session()
                    self.seed(qp, session)
                    self.execute_ok(qp, session, "CREATE TABLE child (id INTEGER PRIMARY KEY, pid INTEGER REFERENCES item(id));")
                    self.execute_ok(qp, session, "CREATE TABLE leaf (id INTEGER REFERENCES child(id));")
                    self.execute_ok(qp, session, "INSERT INTO child VALUES (2, 1);")
                    self.execute_ok(qp, session, "INSERT INTO leaf VALUES (2);")
                    drop = qp._sm.drop_table

                    def fail_child(table):
                        if table == "child":
                            raise StorageError("drop child gagal")
                        return drop(table)

                    with patch.object(qp._sm, "drop_table", side_effect=fail_child):
                        result = qp.execute_query("DROP TABLE item CASCADE;", session)
                    self.assertFalse(result.ok)
                    self.assertIn("drop child gagal", result.message)
                    self.assertEqual(set(qp._sm.list_tables()), {"item", "child"})
                with runtime(directory, protocol) as qp:
                    session = qp.open_session()
                    self.assertEqual(self.execute_ok(qp, session, "SELECT * FROM child;").rows, [[2, 1]])
                    self.assertEqual(set(qp._sm.list_tables()), {"item", "child"})

    def test_process_exit_during_storage_mutation_recovers_without_old_redo(self) -> None:
        for protocol in ("lock", "timestamp"):
            for mode in ("drop", "create", "cascade"):
                with self.subTest(protocol=protocol, mode=mode), TemporaryDirectory() as directory:
                    child = subprocess.run(
                        [sys.executable, "-c", CRASH_DURING_DDL, directory, mode, protocol],
                        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=15,
                    )
                    self.assertEqual(child.returncode, 73, child.stdout + child.stderr)
                    for _ in range(2):
                        with runtime(directory, protocol) as qp:
                            session = qp.open_session()
                            expected = [] if mode == "drop" else ["item"]
                            self.assertEqual(qp._sm.list_tables(), expected)
                            if mode != "drop":
                                rows = self.execute_ok(qp, session, "SELECT * FROM item;").rows
                                self.assertEqual(rows, [] if mode == "create" else [[7]])


if __name__ == "__main__":
    unittest.main()
