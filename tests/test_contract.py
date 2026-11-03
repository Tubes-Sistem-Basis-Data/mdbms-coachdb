from __future__ import annotations

import copy
import inspect
import json
import pickle
import tempfile
import unittest
from typing import Iterator

from common import (
    NO_RECORD,
    AccessResponse,
    Action,
    CheckpointResult,
    ExecutionResult,
    IndexInfo,
    LogKind,
    LogRecord,
    LogResult,
    ParsedQuery,
    PlanNode,
    ProtocolType,
    QueryKind,
    RecoveryResult,
    RecoveryTarget,
    ResourceId,
    RID,
    TableSchema,
    TableStats,
    TxResult,
)
from common.interfaces import (
    ConcurrencyControlManager,
    FailureRecoveryManager,
    QueryOptimizer,
    QueryProcessor,
    Session,
    StorageManager,
)
from concurrency_control import CCManager
from failure_recovery import FailureRecoveryManager as FRMFacade
from query_optimizer import QueryOptimizerFacade
from query_processor.facade import QueryProcessorFacade
from storage_manager.facade import StorageManager as SMFacade

PAIRS = [
    (QueryProcessorFacade, QueryProcessor),
    (QueryOptimizerFacade, QueryOptimizer),
    (SMFacade, StorageManager),
    (CCManager, ConcurrencyControlManager),
    (FRMFacade, FailureRecoveryManager),
]


class TestFacadeConformance(unittest.TestCase):
    def test_subclass_and_no_abstract_left(self):
        for facade, abc in PAIRS:
            with self.subTest(facade=facade.__name__):
                self.assertTrue(issubclass(facade, abc))
                self.assertFalse(inspect.isabstract(facade))
                self.assertEqual(set(getattr(facade, "__abstractmethods__", ())), set())

    def test_signatures_match_contract(self):
        for facade, abc in PAIRS:
            for name in abc.__abstractmethods__:
                with self.subTest(method=f"{facade.__name__}.{name}"):
                    expected = list(inspect.signature(getattr(abc, name)).parameters)
                    actual = list(inspect.signature(getattr(facade, name)).parameters)
                    self.assertEqual(actual, expected)


class TestReturnTypes(unittest.TestCase):
    """Setiap method facade bisa dipanggil dan mengembalikan tipe sesuai kontrak."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.sm = SMFacade(self._tmp.name)
        self.frm = FRMFacade()
        self.ccm = CCManager(self.frm)
        self.frm.set_dependencies(self.sm, self.ccm)
        self.sm.set_log_flusher(self.frm.flush_log)
        self.qo = QueryOptimizerFacade(self.sm)
        self.qp = QueryProcessorFacade(self.qo, self.sm, self.ccm, self.frm)
        self.rid = RID("student", 0, 0)

    def tearDown(self):
        self._tmp.cleanup()

    def test_query_processor(self):
        session = self.qp.open_session()
        self.assertIsInstance(session, Session)
        self.assertIsInstance(self.qp.execute_query("SELECT * FROM student;", session), ExecutionResult)
        self.assertIsNone(self.qp.close_session(session.session_id))

    def test_query_optimizer(self):
        parsed = self.qo.parse_query("SELECT * FROM student;")
        self.assertIsInstance(parsed, ParsedQuery)
        self.assertIsInstance(self.qo.optimize_query(parsed), ParsedQuery)
        self.assertIsInstance(self.qo.get_cost(PlanNode()), float)
        self.assertIsInstance(self.qo.explain(parsed), str)

    def test_storage_manager(self):
        sm, rid = self.sm, self.rid
        self.assertIsInstance(sm.list_tables(), list)
        self.assertIsInstance(sm.get_schema("student"), TableSchema)
        self.assertIsInstance(sm.get_stats("student"), TableStats)
        self.assertIsInstance(sm.scan("student"), Iterator)
        self.assertIsNone(sm.fetch("student", rid))
        self.assertIsInstance(sm.allocate_rid("student"), RID)
        self.assertIsNone(sm.release_rid(rid))
        self.assertIsNone(sm.insert("student", rid, [1], 1))
        self.assertIsNone(sm.update("student", rid, [1], None))
        self.assertIsNone(sm.delete("student", rid, None))
        self.assertIsNone(sm.delete_all("student", None))
        self.assertIsNone(sm.create_index(IndexInfo("idx_id", "student", "id")))
        self.assertIsInstance(sm.index_lookup("student", "id", 1), list)
        self.assertIsInstance(sm.index_range("student", "id", None, 10), list)
        self.assertIsNone(sm.flush())

    def test_concurrency_control(self):
        ccm = self.ccm
        self.assertIsNone(ccm.initialize(0))
        tx = ccm.begin_transaction()
        self.assertIsInstance(tx, int)
        resp = ccm.validate_object(tx, ResourceId("student", self.rid), Action.READ)
        self.assertIsInstance(resp, AccessResponse)
        self.assertIsInstance(ccm.commit(tx), TxResult)
        self.assertIsInstance(ccm.abort(tx), TxResult)
        self.assertIsNone(ccm.set_protocol(ProtocolType.LOCK_BASED))
        self.assertIsInstance(ccm.get_protocol(), ProtocolType)
        self.assertIsNone(ccm.pause_new_transactions())
        self.assertIsNone(ccm.wait_until_idle())
        self.assertIsNone(ccm.resume_transactions())

    def test_failure_recovery(self):
        frm = self.frm
        self.assertIsInstance(frm.write_log(LogRecord(LogKind.BEGIN, tx_id=1)), LogResult)
        self.assertIsInstance(frm.flush_log(1), bool)
        self.assertIsInstance(frm.rollback(1), TxResult)
        self.assertIsInstance(frm.checkpoint(), CheckpointResult)
        self.assertIsInstance(frm.recover(), RecoveryResult)
        self.assertIsInstance(frm.recover_to(RecoveryTarget(tx_id=1)), RecoveryResult)
        self.assertIsInstance(frm.read_log(), list)


class TestBootstrap(unittest.TestCase):
    def test_main_bootstrap_jalan(self):
        import main

        with tempfile.TemporaryDirectory() as tmp:
            qp = main.bootstrap(main.parse_args(["--data-dir", tmp]))
        self.assertIsInstance(qp, QueryProcessor)


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

    def test_query_kind_explain(self):
        self.assertEqual(QueryKind.EXPLAIN.value, "EXPLAIN")


if __name__ == "__main__":
    unittest.main()
