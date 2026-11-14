from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from common.catalog import ColumnSchema, DataType
from common.errors import StorageError, TransactionAbortedError
from common.log import LogKind, LogResult
from common.transaction import AccessStatus, Action, ChangeOp, ResourceId, TxResult, TxStatus
from concurrency_control.manager import CCManager
from failure_recovery import FailureRecoveryManager
from failure_recovery.log import codec
from query_optimizer import QueryOptimizerFacade
from query_processor.context import ExecContext
from query_processor.facade import QueryProcessorFacade
from query_processor.operators.insert import InsertExecution
from storage_manager.facade import StorageManager


class TestExecContextIntegration(unittest.TestCase):
    def setUp(self) -> None:
        previous_path = codec.get_log_path()
        self.addCleanup(codec.set_log_path, previous_path)
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.sm = StorageManager(data_dir=temp.name)
        self.addCleanup(self.sm._disk.close)
        # Setup lewat katalog karena facade create_table/close SM masih stub.
        self.sm._catalog.create_table(
            "student",
            (ColumnSchema("id", DataType.INTEGER, nullable=False),
             ColumnSchema("name", DataType.VARCHAR, length=50)),
            ("id",), (),
        )
        self.original = [[1, "old"], [2, "two"]]
        self.sm.bulk_load("student", self.original)
        self.frm = FailureRecoveryManager(log_path=str(Path(temp.name) / "wal.log"))
        self.addCleanup(self.frm.close)
        self.ccm = CCManager(self.frm)
        self.frm.set_dependencies(self.sm, self.ccm)
        self.sm.set_log_flusher(self.frm.flush_log)
        self.qp = QueryProcessorFacade(
            QueryOptimizerFacade(self.sm), self.sm, self.ccm, self.frm
        )
        self.session = self.qp.open_session()
        self.addCleanup(self.qp.close_session, self.session.session_id)

    def execute_ok(self, sql: str):
        result = self.qp.execute_query(sql, self.session)
        self.assertTrue(result.ok, result.message)
        return result

    def assert_abort_restores(self, sql: str, method: str, op: ChangeOp, expected: list) -> None:
        self.execute_ok("BEGIN;")
        tx_id = self.session.tx_id
        write = getattr(self.sm, method)
        logged_lsns = []

        def write_after_log(*args):
            record = self.frm.read_log(tx_id)[-1]
            self.assertIs(record.kind, LogKind.DATA)
            self.assertIs(record.change.op, op)
            self.assertEqual(record.change.rid, args[1])
            self.assertEqual(record.lsn, args[-1])
            logged_lsns.append(record.lsn)
            return write(*args)

        with patch.object(self.sm, method, side_effect=write_after_log):
            result = self.execute_ok(sql)

        self.assertEqual(result.affected_rows, len(logged_lsns))
        self.assertEqual(len(set(logged_lsns)), len(logged_lsns))
        self.assertEqual([row.values for row in self.sm.scan("student")], expected)
        self.execute_ok("ABORT;")
        self.assertIsNone(self.session.tx_id)
        rows = self.execute_ok("SELECT id, name FROM student;").rows
        self.assertEqual(rows, self.original)
        records = self.frm.read_log(tx_id)
        self.assertEqual(sum(record.kind is LogKind.DATA for record in records), result.affected_rows)
        self.assertEqual([record.kind for record in records[-2:]],
                         [LogKind.ABORT_REQUEST, LogKind.ABORT])

    def test_insert_logs_before_storage_and_abort_removes_the_row(self) -> None:
        self.assert_abort_restores(
            "INSERT INTO student VALUES (3, 'new');", "insert", ChangeOp.INSERT,
            self.original + [[3, "new"]],
        )

    def test_update_logs_before_storage_and_abort_restores_before_image(self) -> None:
        self.assert_abort_restores(
            "UPDATE student SET name = 'new' WHERE id = 1;", "update", ChangeOp.UPDATE,
            [[1, "new"], [2, "two"]],
        )

    def test_delete_without_where_logs_each_row_and_abort_restores_all(self) -> None:
        self.assert_abort_restores("DELETE FROM student;", "delete", ChangeOp.DELETE, [])

    def test_commit_keeps_the_logged_change(self) -> None:
        self.execute_ok("BEGIN;")
        tx_id = self.session.tx_id
        self.execute_ok("UPDATE student SET name = 'new' WHERE id = 1;")
        self.execute_ok("COMMIT;")

        self.assertEqual(self.execute_ok("SELECT name FROM student WHERE id = 1;").rows, [["new"]])
        self.assertEqual([record.kind for record in self.frm.read_log(tx_id)],
                         [LogKind.BEGIN, LogKind.DATA, LogKind.COMMIT])

    def test_autocommit_writes_data_and_commit_to_wal(self) -> None:
        self.execute_ok("INSERT INTO student VALUES (3, 'new');")

        self.assertIsNone(self.session.tx_id)
        self.assertEqual([record.kind for record in self.frm.read_log()],
                         [LogKind.BEGIN, LogKind.DATA, LogKind.COMMIT])
        self.assertEqual([row.values for row in self.sm.scan("student")],
                         self.original + [[3, "new"]])

    def test_select_authorizes_access_without_data_log(self) -> None:
        with patch.object(self.ccm, "validate_object", wraps=self.ccm.validate_object) as authorize:
            self.assertEqual(self.execute_ok("SELECT id, name FROM student;").rows, self.original)

        self.assertTrue(authorize.called)
        self.assertTrue(all(call.args[2] is Action.READ for call in authorize.call_args_list))
        self.assertFalse(any(record.kind is LogKind.DATA for record in self.frm.read_log()))

    def test_failure_after_one_write_undoes_the_partial_statement(self) -> None:
        result = self.qp.execute_query("UPDATE student SET id = 3;", self.session)

        self.assertFalse(result.ok)
        self.assertIsNone(self.session.tx_id)
        self.assertEqual([row.values for row in self.sm.scan("student")], self.original)
        self.assertEqual([record.kind for record in self.frm.read_log()],
                         [LogKind.BEGIN, LogKind.DATA, LogKind.ABORT_REQUEST, LogKind.ABORT])

    def assert_rollback_retry_restores_partial_write(self, explicit: bool) -> None:
        if explicit:
            self.execute_ok("BEGIN;")
        update = self.sm.update

        def fail_second_write(table, rid, values, lsn):
            if rid.slot == 1:
                raise StorageError("write gagal")
            return update(table, rid, values, lsn)

        with patch.object(self.sm, "update", side_effect=fail_second_write), \
             patch.object(self.frm, "rollback", side_effect=lambda tx: TxResult(
                 tx, TxStatus.FAILED, False, "undo gagal"
             )) as rollback:
            result = self.qp.execute_query("UPDATE student SET name = 'new';", self.session)

        self.assertFalse(result.ok)
        self.assertIn("write gagal", result.message)
        self.assertIn("undo gagal", result.message)
        tx_id = self.session.tx_id
        self.assertIsNotNone(tx_id)
        self.assertEqual(self.session.in_explicit_tx, explicit)
        rollback.assert_called_once_with(tx_id)
        self.assertEqual([row.values for row in self.sm.scan("student")],
                         [[1, "new"], [2, "two"]])

        with patch.object(self.qp._qo, "parse_query", wraps=self.qp._qo.parse_query) as parse:
            for sql in ("BEGIN;", "COMMIT;", "SELECT * FROM student;", "DELETE FROM student;"):
                with self.subTest(sql=sql):
                    blocked = self.qp.execute_query(sql, self.session)
                    self.assertFalse(blocked.ok)
                    self.assertIn("Rollback", blocked.message)
                    self.assertEqual(self.session.tx_id, tx_id)
            parse.assert_not_called()

        other_id = self.ccm.begin_transaction()
        self.addCleanup(self.ccm.abort, other_id)
        resource = ResourceId("student")
        self.assertIs(self.ccm.validate_object(other_id, resource, Action.READ).status,
                      AccessStatus.WAIT)
        self.execute_ok("ABORT;")
        self.assertIsNone(self.session.tx_id)
        self.assertFalse(self.session.in_explicit_tx)
        self.assertEqual([row.values for row in self.sm.scan("student")], self.original)
        self.assertIs(self.ccm.validate_object(other_id, resource, Action.READ).status,
                      AccessStatus.ALLOWED)
        self.assertEqual([record.kind for record in self.frm.read_log(tx_id)],
                         [LogKind.BEGIN, LogKind.DATA, LogKind.DATA,
                          LogKind.ABORT_REQUEST, LogKind.ABORT])

    def test_explicit_failed_rollback_retains_locks_until_abort_retry(self) -> None:
        self.assert_rollback_retry_restores_partial_write(explicit=True)

    def test_autocommit_failed_rollback_retains_locks_until_abort_retry(self) -> None:
        self.assert_rollback_retry_restores_partial_write(explicit=False)

    def test_disconnect_failed_rollback_keeps_session_for_cleanup_retry(self) -> None:
        self.execute_ok("BEGIN;")
        self.execute_ok("UPDATE student SET name = 'new' WHERE id = 1;")
        tx_id = self.session.tx_id
        with patch.object(self.frm, "rollback", return_value=TxResult(
            tx_id, TxStatus.FAILED, False, "undo gagal"
        )) as rollback, self.assertLogs("coachdb.qp", level="ERROR"):
            self.qp.close_session(self.session.session_id)

        rollback.assert_called_once_with(tx_id)
        self.assertEqual(self.session.tx_id, tx_id)
        self.assertIs(self.qp._sessions.get_session(self.session.session_id), self.session)
        self.qp.close_session(self.session.session_id)
        self.assertIsNone(self.session.tx_id)
        self.assertIsNone(self.qp._sessions.get_session(self.session.session_id))
        self.assertEqual([row.values for row in self.sm.scan("student")], self.original)

    def test_real_lock_wait_retries_without_duplicate_data_logs(self) -> None:
        reader_id = self.ccm.begin_transaction()
        reader = ExecContext(reader_id, self.sm, self.ccm)
        self.assertIs(reader.authorize_table("student").status, AccessStatus.ALLOWED)
        writer_id = self.ccm.begin_transaction()
        writer = ExecContext(writer_id, self.sm, self.ccm)

        def release_reader(_):
            self.assertTrue(self.ccm.commit(reader_id).ok)

        with patch("query_processor.context.sleep", side_effect=release_reader) as sleep:
            InsertExecution("student", (3, "new")).execute(writer)

        sleep.assert_called_once()
        self.assertEqual([record.kind for record in self.frm.read_log(writer_id)],
                         [LogKind.BEGIN, LogKind.DATA])
        self.assertTrue(self.ccm.abort(writer_id).ok)
        self.assertEqual([row.values for row in self.sm.scan("student")], self.original)

    def test_log_failure_does_not_write_storage_or_repeat_ccm_abort(self) -> None:
        tx_id = self.ccm.begin_transaction()
        ctx = ExecContext(tx_id, self.sm, self.ccm)
        write_log = self.frm.write_log

        def fail_data_log(record):
            if record.kind is LogKind.DATA:
                return LogResult(ok=False, message="WAL gagal")
            return write_log(record)

        with patch.object(self.frm, "write_log", side_effect=fail_data_log), \
             patch.object(self.sm, "insert", wraps=self.sm.insert) as insert, \
             patch.object(self.sm, "release_rid", wraps=self.sm.release_rid) as release, \
             patch.object(self.ccm, "abort", wraps=self.ccm.abort) as abort:
            with self.assertRaises(TransactionAbortedError):
                InsertExecution("student", (3, "new")).execute(ctx)

        insert.assert_not_called()
        release.assert_called_once()
        abort.assert_not_called()
        self.assertEqual([row.values for row in self.sm.scan("student")], self.original)
        self.assertEqual([record.kind for record in self.frm.read_log(tx_id)],
                         [LogKind.BEGIN, LogKind.ABORT_REQUEST, LogKind.ABORT])


if __name__ == "__main__":
    unittest.main()
