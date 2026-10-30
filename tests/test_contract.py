"""Uji kontrak: mock turunan ABC yang benar dan alur dasar berjalan."""

from __future__ import annotations

import copy
import inspect
import json
import pickle
import unittest

from common import (
    NO_RECORD,
    AccessStatus,
    Action,
    ChangeOp,
    DataChange,
    ExecutionResult,
    LogKind,
    ProtocolType,
    QueryKind,
    QuerySyntaxError,
    RecoveryTarget,
    ResourceId,
    RID,
    SemanticError,
    TxStatus,
)
from common.interfaces import (
    ConcurrencyControlManager,
    FailureRecoveryManager,
    QueryOptimizer,
    QueryProcessor,
    StorageManager,
)
from mocks import (
    MockConcurrencyControlManager,
    MockFailureRecoveryManager,
    MockQueryOptimizer,
    MockQueryProcessor,
    MockStorageManager,
)

PAIRS = [
    (MockQueryProcessor, QueryProcessor),
    (MockQueryOptimizer, QueryOptimizer),
    (MockStorageManager, StorageManager),
    (MockConcurrencyControlManager, ConcurrencyControlManager),
    (MockFailureRecoveryManager, FailureRecoveryManager),
]


def build():
    sm = MockStorageManager()
    frm = MockFailureRecoveryManager()
    ccm = MockConcurrencyControlManager(frm)
    frm.set_dependencies(sm, ccm)
    sm.set_log_flusher(frm.flush_log)
    ccm.initialize(frm.recover().last_tx_id)
    qo = MockQueryOptimizer(sm)
    qp = MockQueryProcessor(qo, sm, ccm)
    return sm, frm, ccm, qo, qp


class TestMockConformance(unittest.TestCase):
    def test_subclass_and_no_abstract_left(self):
        for mock, abc in PAIRS:
            with self.subTest(mock=mock.__name__):
                self.assertTrue(issubclass(mock, abc))
                self.assertFalse(inspect.isabstract(mock))
                self.assertEqual(set(getattr(mock, "__abstractmethods__", ())), set())

    def test_signatures_match_contract(self):
        for mock, abc in PAIRS:
            for name in abc.__abstractmethods__:
                with self.subTest(method=f"{mock.__name__}.{name}"):
                    expected = list(inspect.signature(getattr(abc, name)).parameters)
                    actual = list(inspect.signature(getattr(mock, name)).parameters)
                    self.assertEqual(actual, expected)


class TestSharedTypes(unittest.TestCase):
    def test_no_record_singleton(self):
        self.assertIsNot(NO_RECORD, None)
        self.assertEqual(repr(NO_RECORD), "NO_RECORD")
        self.assertIs(copy.copy(NO_RECORD), NO_RECORD)
        self.assertIs(copy.deepcopy(NO_RECORD), NO_RECORD)
        self.assertIs(pickle.loads(pickle.dumps(NO_RECORD)), NO_RECORD)

    def test_rid_hashable(self):
        self.assertEqual(len({RID("t", 0, 1), RID("t", 0, 1)}), 1)
        self.assertEqual(len({ResourceId("t"), ResourceId("t")}), 1)

    def test_recovery_target_exactly_one(self):
        RecoveryTarget(tx_id=3)
        RecoveryTarget(timestamp=1.0)
        with self.assertRaises(ValueError):
            RecoveryTarget()
        with self.assertRaises(ValueError):
            RecoveryTarget(timestamp=1.0, tx_id=3)

    def test_execution_result_json_roundtrip(self):
        res = ExecutionResult(ok=True, columns=["a"], rows=[[1, None]], affected_rows=0, message="x")
        self.assertEqual(ExecutionResult.from_dict(json.loads(json.dumps(res.to_dict()))), res)


class TestSmokeFlow(unittest.TestCase):
    def setUp(self):
        self.sm, self.frm, self.ccm, self.qo, self.qp = build()
        self.session = self.qp.open_session()

    def test_select(self):
        res = self.qp.execute_query("SELECT * FROM student;", self.session)
        self.assertTrue(res.ok, res.message)
        self.assertEqual(res.columns, ["id", "name", "gpa", "gender"])
        self.assertEqual(len(res.rows), 5)
        self.assertIsNone(self.session.tx_id)
        kinds = [r.kind for r in self.frm.read_log()]
        self.assertEqual(kinds, [LogKind.BEGIN, LogKind.COMMIT])

    def test_sample_tables_and_stats(self):
        self.assertEqual(set(self.sm.list_tables()), {"student", "course", "enrollment"})
        stats = self.sm.get_stats("student")
        self.assertEqual(stats.n_r, 5)
        self.assertEqual(stats.distinct["id"], 5)
        self.assertGreaterEqual(stats.b_r, 1)

    def test_errors_become_result(self):
        res = self.qp.execute_query("SELECT name FROM student;", self.session)
        self.assertFalse(res.ok)
        self.assertIn("mock: belum didukung", res.message)
        res = self.qp.execute_query("SELECT * FROM dosen;", self.session)
        self.assertFalse(res.ok)
        with self.assertRaises(QuerySyntaxError):
            self.qo.parse_query("DROP TABLE student;")
        with self.assertRaises(SemanticError):
            self.qo.parse_query("SELECT * FROM dosen;")

    def test_begin_abort_via_qp(self):
        self.assertTrue(self.qp.execute_query("BEGIN TRANSACTION;", self.session).ok)
        tx_id = self.session.tx_id
        self.assertIsNotNone(tx_id)
        self.assertTrue(self.qp.execute_query("SELECT * FROM course;", self.session).ok)
        self.assertEqual(self.session.tx_id, tx_id)
        self.assertTrue(self.qp.execute_query("ABORT;", self.session).ok)
        self.assertIsNone(self.session.tx_id)
        kinds = [r.kind for r in self.frm.read_log(tx_id)]
        self.assertEqual(kinds, [LogKind.BEGIN, LogKind.ABORT_REQUEST, LogKind.ABORT])

    def test_explain(self):
        parsed = self.qo.parse_query("EXPLAIN SELECT * FROM enrollment;")
        self.assertEqual(parsed.kind, QueryKind.EXPLAIN)
        res = self.qp.execute_query("EXPLAIN SELECT * FROM enrollment;", self.session)
        self.assertTrue(res.ok)
        self.assertIn("TableScan(enrollment)", res.message)

    def test_insert_two_step_commit(self):
        tx = self.ccm.begin_transaction()
        rid = self.sm.allocate_rid("student")
        values = [6, "Fajar", 3.10, "L"]
        resp = self.ccm.validate_object(
            tx, ResourceId("student", rid), Action.WRITE,
            DataChange("student", rid, ChangeOp.INSERT, NO_RECORD, values),
        )
        self.assertEqual(resp.status, AccessStatus.ALLOWED)
        self.assertIsNotNone(resp.lsn)
        self.sm.insert("student", rid, values, resp.lsn)
        self.assertEqual(self.ccm.commit(tx).status, TxStatus.COMMITTED)
        self.assertEqual(self.sm.fetch("student", rid).values, values)
        self.assertEqual(len(list(self.sm.scan("student"))), 6)

    def test_update_then_abort_restores(self):
        row = next(self.sm.scan("student"))
        before = list(row.values)
        after = [before[0], before[1], 4.0, before[3]]
        tx = self.ccm.begin_transaction()
        change = DataChange("student", row.rid, ChangeOp.UPDATE, before, after)
        resp = self.ccm.validate_object(tx, ResourceId("student", row.rid), Action.WRITE, change)
        again = self.ccm.validate_object(tx, ResourceId("student", row.rid), Action.WRITE, change)
        self.assertEqual(again.lsn, resp.lsn)
        self.sm.update("student", row.rid, after, resp.lsn)
        self.assertEqual(self.sm.fetch("student", row.rid).values, after)
        self.assertEqual(len([r for r in self.frm.read_log(tx) if r.kind is LogKind.DATA]), 1)
        self.assertTrue(self.ccm.abort(tx).ok)
        self.assertEqual(self.sm.fetch("student", row.rid).values, before)

    def test_insert_and_delete_abort(self):
        tx = self.ccm.begin_transaction()
        rid = self.sm.allocate_rid("course")
        values = [106, "Jaringan", 3, "IF"]
        resp = self.ccm.validate_object(
            tx, ResourceId("course", rid), Action.WRITE,
            DataChange("course", rid, ChangeOp.INSERT, NO_RECORD, values),
        )
        self.sm.insert("course", rid, values, resp.lsn)
        victim = next(self.sm.scan("course"))
        resp = self.ccm.validate_object(
            tx, ResourceId("course", victim.rid), Action.WRITE,
            DataChange("course", victim.rid, ChangeOp.DELETE, victim.values, NO_RECORD),
        )
        self.sm.delete("course", victim.rid, resp.lsn)
        self.ccm.abort(tx)
        self.assertIsNone(self.sm.fetch("course", rid))
        self.assertEqual(self.sm.fetch("course", victim.rid).values, victim.values)
        self.assertEqual(len(list(self.sm.scan("course"))), 5)

    def test_protocol_switch_blocked_by_active_tx(self):
        tx = self.ccm.begin_transaction()
        with self.assertRaises(Exception):
            self.ccm.set_protocol(ProtocolType.TIMESTAMP_BASED)
        self.ccm.commit(tx)
        self.ccm.set_protocol(ProtocolType.TIMESTAMP_BASED)
        self.assertEqual(self.ccm.get_protocol(), ProtocolType.TIMESTAMP_BASED)

    def test_tx_id_continues_after_initialize(self):
        self.ccm.initialize(41)
        self.assertEqual(self.ccm.begin_transaction(), 42)


if __name__ == "__main__":
    unittest.main()
