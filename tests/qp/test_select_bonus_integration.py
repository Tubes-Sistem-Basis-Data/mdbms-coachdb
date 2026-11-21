from __future__ import annotations

from contextlib import contextmanager, redirect_stdout
import io
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common.catalog import ColumnSchema, DataType, TableSchema
from common.log import LogKind
from failure_recovery.log import codec
from main import bootstrap, close_runtime, parse_args
from storage_manager.facade import StorageManager


ORIGINAL = [[1, "Ada", 3.0], [2, "Budi", 4.0], [3, "Cici", 2.5]]


@contextmanager
def runtime(protocol):
    previous_log_path = codec.get_log_path()
    try:
        with TemporaryDirectory() as directory:
            sm = StorageManager(directory)
            try:
                sm.create_table(TableSchema("student", [
                    ColumnSchema("id", DataType.INTEGER, nullable=False),
                    ColumnSchema("name", DataType.VARCHAR, length=50),
                    ColumnSchema("gpa", DataType.FLOAT),
                ], primary_key=["id"]))
                sm.create_table(TableSchema("threshold", [ColumnSchema("gpa", DataType.FLOAT)]))
                sm.bulk_load("student", ORIGINAL)
                sm.bulk_load("threshold", [[3.2]])
            finally:
                sm.close()
            with redirect_stdout(io.StringIO()):
                qp = bootstrap(parse_args(["--data-dir", directory, "--protocol", protocol]))
            try:
                yield qp, qp.open_session()
            finally:
                close_runtime(qp)
    finally:
        codec.set_log_path(previous_log_path)


class TestSelectBonusIntegration(unittest.TestCase):
    def execute_ok(self, qp, session, sql):
        result = qp.execute_query(sql, session)
        self.assertTrue(result.ok, result.message)
        return result

    def test_limit_alias_filter_sort_and_empty_header_through_real_qo(self) -> None:
        for protocol in ("lock", "timestamp"):
            with self.subTest(protocol=protocol), runtime(protocol) as (qp, session):
                cases = (
                    ("SELECT s.name AS nama, s.id FROM student s WHERE s.id >= 1 "
                     "ORDER BY s.gpa DESC LIMIT 2;", ["nama", "id"], [["Budi", 2], ["Ada", 1]]),
                    ("SELECT id AS nim FROM student WHERE id > 100 LIMIT 10;", ["nim"], []),
                    ("SELECT id AS x, name AS x FROM student WHERE id = 1;", ["x", "x"], [[1, "Ada"]]),
                    ("SELECT a.name AS nama FROM student a JOIN student b ON a.id = b.id "
                     "ORDER BY a.id LIMIT 2;", ["nama"], [["Ada"], ["Budi"]]),
                )
                for sql, columns, rows in cases:
                    result = self.execute_ok(qp, session, sql)
                    self.assertEqual((result.columns, result.rows), (columns, rows))
                self.assertIsNone(session.tx_id)
                self.assertFalse(any(record.kind is LogKind.DATA for record in qp._frm.read_log()))

    def test_limit_zero_does_not_execute_blocking_child_or_ask_for_read_access(self) -> None:
        for protocol in ("lock", "timestamp"):
            with self.subTest(protocol=protocol), runtime(protocol) as (qp, session):
                with patch.object(qp._sm, "scan", wraps=qp._sm.scan) as scan, \
                     patch.object(qp._ccm, "validate_object", wraps=qp._ccm.validate_object) as authorize:
                    result = self.execute_ok(qp, session,
                        "SELECT id AS nim FROM student WHERE gpa / 0 > 1 ORDER BY id LIMIT 0;")
                self.assertEqual((result.columns, result.rows), (["nim"], []))
                scan.assert_not_called()
                authorize.assert_not_called()

    def test_scalar_limit_one_and_zero_follow_cardinality_and_null_rules(self) -> None:
        for protocol in ("lock", "timestamp"):
            with self.subTest(protocol=protocol), runtime(protocol) as (qp, session):
                result = self.execute_ok(qp, session,
                    "SELECT id AS nim FROM student WHERE gpa > "
                    "(SELECT gpa AS minimum FROM student ORDER BY gpa LIMIT 1) ORDER BY id;")
                self.assertEqual((result.columns, result.rows), (["nim"], [[1], [2]]))
                result = self.execute_ok(qp, session,
                    "SELECT id AS nim FROM student WHERE gpa > "
                    "(SELECT gpa FROM threshold WHERE gpa / 0 > 1 ORDER BY gpa LIMIT 0);")
                self.assertEqual((result.columns, result.rows), (["nim"], []))

    def test_scalar_limit_two_reports_error_then_session_can_continue(self) -> None:
        for protocol in ("lock", "timestamp"):
            with self.subTest(protocol=protocol), runtime(protocol) as (qp, session):
                result = qp.execute_query("SELECT id FROM student WHERE id = "
                                          "(SELECT id FROM student LIMIT 2);", session)
                self.assertFalse(result.ok)
                self.assertIn("lebih dari satu baris", result.message)
                self.assertIsNone(session.tx_id)
                self.assertEqual(self.execute_ok(qp, session,
                    "SELECT id, name, gpa FROM student ORDER BY id;").rows, ORIGINAL)

    def test_update_and_delete_with_limited_scalar_use_outer_transaction_and_can_abort(self) -> None:
        for protocol in ("lock", "timestamp"):
            with self.subTest(protocol=protocol), runtime(protocol) as (qp, session):
                for sql in (
                    "UPDATE student SET name = 'selected' WHERE gpa > (SELECT gpa FROM threshold LIMIT 1);",
                    "DELETE FROM student WHERE gpa > (SELECT gpa FROM threshold LIMIT 1);",
                ):
                    with patch.object(qp._ccm, "begin_transaction", wraps=qp._ccm.begin_transaction) as begin:
                        self.execute_ok(qp, session, "BEGIN;")
                        tx_id = session.tx_id
                        result = self.execute_ok(qp, session, sql)
                        self.assertEqual(result.affected_rows, 1)
                        self.assertEqual(session.tx_id, tx_id)
                        begin.assert_called_once()
                        self.execute_ok(qp, session, "ABORT;")
                    self.assertEqual([r.kind for r in qp._frm.read_log(tx_id)],
                                     [LogKind.BEGIN, LogKind.DATA, LogKind.ABORT_REQUEST, LogKind.ABORT])
                    self.assertEqual(self.execute_ok(qp, session,
                        "SELECT id, name, gpa FROM student ORDER BY id;").rows, ORIGINAL)

    def test_scalar_cache_is_once_per_statement_in_the_same_explicit_transaction(self) -> None:
        for protocol in ("lock", "timestamp"):
            with self.subTest(protocol=protocol), runtime(protocol) as (qp, session):
                self.execute_ok(qp, session, "BEGIN;")
                tx_id = session.tx_id
                sql = "SELECT id FROM student WHERE gpa > (SELECT gpa FROM threshold LIMIT 1) ORDER BY id;"
                with patch.object(qp._sm, "scan", wraps=qp._sm.scan) as scan:
                    self.assertEqual(self.execute_ok(qp, session, sql).rows, [[2]])
                    self.execute_ok(qp, session, "UPDATE threshold SET gpa = 2.0;")
                    self.assertEqual(self.execute_ok(qp, session, sql).rows, [[1], [2], [3]])
                self.assertEqual(sum(call.args == ("threshold",) for call in scan.call_args_list), 3)
                self.assertEqual(session.tx_id, tx_id)
                self.execute_ok(qp, session, "ABORT;")
                self.assertEqual([row.values for row in qp._sm.scan("threshold")], [[3.2]])

    def test_short_circuit_does_not_run_a_limited_scalar_that_would_fail(self) -> None:
        for protocol in ("lock", "timestamp"):
            with self.subTest(protocol=protocol), runtime(protocol) as (qp, session):
                with patch.object(qp._sm, "scan", wraps=qp._sm.scan) as scan:
                    result = self.execute_ok(qp, session, "SELECT id FROM student WHERE id < 0 AND "
                                             "gpa > (SELECT gpa FROM threshold WHERE gpa / 0 > 1 LIMIT 1);")
                self.assertEqual(result.rows, [])
                self.assertFalse(any(call.args == ("threshold",) for call in scan.call_args_list))

    def test_correlated_and_unsupported_bonus_queries_remain_rejected(self) -> None:
        for protocol in ("lock", "timestamp"):
            with self.subTest(protocol=protocol), runtime(protocol) as (qp, session):
                for sql in (
                    "SELECT s.id FROM student s WHERE s.gpa > (SELECT gpa FROM threshold WHERE gpa > s.gpa LIMIT 1);",
                    "SELECT id FROM student WHERE id IN (SELECT id FROM student LIMIT 1);",
                ):
                    with self.subTest(sql=sql):
                        self.assertFalse(qp.execute_query(sql, session).ok)
                        self.assertIsNone(session.tx_id)
                self.assertEqual(self.execute_ok(qp, session,
                    "SELECT id AS nim FROM student ORDER BY id LIMIT 1;").rows, [[1]])


if __name__ == "__main__":
    unittest.main()
