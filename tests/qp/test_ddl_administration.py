from __future__ import annotations

import unittest
from unittest.mock import Mock

from common.errors import StorageError
from common.catalog import ColumnSchema, DataType, TableSchema
from common.interfaces import ConcurrencyControlManager, QueryOptimizer, StorageManager
from common.log import CheckpointResult
from common.plan import CreateTable, ParsedQuery, QueryKind
from common.result import ExecutionResult
from query_processor.administration import AdministrationGate
from query_processor.ddl import DDLCoordinator
from query_processor.errors import ExecutionError
from query_processor.facade import QueryProcessorFacade
from query_processor.session import SessionManager


class TestDDLAdministration(unittest.TestCase):
    def setUp(self) -> None:
        self.sessions = SessionManager()
        self.session = self.sessions.open_session()
        self.gate = AdministrationGate(self.sessions)

    def test_checkpoint_fences_surround_storage_work(self) -> None:
        order = []

        def checkpoint():
            order.append("checkpoint")
            return CheckpointResult(ok=True, lsn=len(order))

        def work():
            order.append("work")
            return ExecutionResult(ok=True)

        coordinator = DDLCoordinator(allow_ddl=True, gate=self.gate, checkpoint=checkpoint)
        with self.gate.request():
            result = coordinator.run(self.session, work)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(order, ["checkpoint", "work", "checkpoint"])

    def test_failed_pre_checkpoint_never_calls_storage_and_releases_gate(self) -> None:
        work = Mock(return_value=ExecutionResult(ok=True))
        checkpoint = Mock(return_value=CheckpointResult(ok=False, message="snapshot gagal"))
        coordinator = DDLCoordinator(allow_ddl=True, gate=self.gate, checkpoint=checkpoint)

        result = coordinator.run(self.session, work)

        self.assertFalse(result.ok)
        self.assertIn("sebelum DDL", result.message)
        work.assert_not_called()
        checkpoint.assert_called_once()
        with self.gate.request():
            pass

    def test_failed_post_checkpoint_does_not_report_success_or_rollback(self) -> None:
        work = Mock(return_value=ExecutionResult(ok=True))
        checkpoint = Mock(side_effect=[CheckpointResult(ok=True, lsn=1),
                                       CheckpointResult(ok=False, message="snapshot gagal")])
        coordinator = DDLCoordinator(allow_ddl=True, gate=self.gate, checkpoint=checkpoint)

        result = coordinator.run(self.session, work)

        self.assertFalse(result.ok)
        self.assertIn("setelah DDL", result.message)
        self.assertIn("mungkin sudah diterapkan", result.message)
        work.assert_called_once()
        self.assertEqual(checkpoint.call_count, 2)
        with self.gate.request():
            pass

    def test_storage_and_followup_checkpoint_errors_are_both_preserved(self) -> None:
        checkpoint = Mock(side_effect=[CheckpointResult(ok=True, lsn=1),
                                       CheckpointResult(ok=False, message="snapshot gagal")])
        coordinator = DDLCoordinator(allow_ddl=True, gate=self.gate, checkpoint=checkpoint)

        result = coordinator.run(self.session, Mock(side_effect=StorageError("drop child gagal")))

        self.assertFalse(result.ok)
        self.assertIn("drop child gagal", result.message)
        self.assertIn("snapshot gagal", result.message)
        self.assertEqual(checkpoint.call_count, 2)

    def test_invalid_checkpoint_response_fails_before_storage(self) -> None:
        work = Mock(return_value=ExecutionResult(ok=True))
        for response in (None, True, ExecutionResult(ok=True), CheckpointResult(ok=True),
                         CheckpointResult(ok=True, lsn=0), CheckpointResult(ok=True, lsn=True)):
            with self.subTest(response=response):
                coordinator = DDLCoordinator(
                    allow_ddl=True, gate=self.gate, checkpoint=Mock(return_value=response),
                )
                result = coordinator.run(self.session, work)
                self.assertFalse(result.ok)
                self.assertIn("tidak valid", result.message)
        work.assert_not_called()

    def test_default_facade_requires_recovery_before_enabling_ddl(self) -> None:
        sm = Mock(spec=StorageManager)
        ccm = Mock(spec=ConcurrencyControlManager)
        qo = Mock(spec=QueryOptimizer)
        qo.parse_query.return_value = ParsedQuery(
            "CREATE TABLE item (id INTEGER);", QueryKind.CREATE_TABLE,
            CreateTable(schema=TableSchema("item", [ColumnSchema("id", DataType.INTEGER)])),
        )
        qp = QueryProcessorFacade(qo, sm, ccm)
        result = qp.execute_query("CREATE TABLE item (id INTEGER);", qp.open_session())
        self.assertFalse(result.ok)
        self.assertIn("DDL", result.message)
        self.assertEqual(sm.mock_calls, [])
        self.assertEqual(ccm.mock_calls, [])

    def test_active_sessions_and_nested_requests_cannot_enter_administration(self) -> None:
        other = self.sessions.open_session()
        for tx_id, explicit in ((7, True), (7, False), (None, True)):
            other.tx_id, other.in_explicit_tx = tx_id, explicit
            with self.subTest(tx_id=tx_id, explicit=explicit), self.assertRaises(ExecutionError):
                with self.gate.exclusive():
                    self.fail("administration entered with an active session")
        other.tx_id, other.in_explicit_tx = None, False
        with self.gate.request(), self.gate.request(), self.assertRaises(ExecutionError):
            with self.gate.exclusive():
                self.fail("nested request entered administration")

    def test_administrative_error_releases_gate_and_keeps_other_requests_out(self) -> None:
        with self.assertRaisesRegex(StorageError, "failure"):
            with self.gate.exclusive():
                with self.assertRaises(ExecutionError):
                    with self.gate.request():
                        self.fail("request entered administration")
                raise StorageError("failure")
        with self.gate.request(), self.gate.exclusive():
            pass


if __name__ == "__main__":
    unittest.main()
