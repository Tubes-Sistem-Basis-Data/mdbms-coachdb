from __future__ import annotations

import threading
import unittest
from unittest.mock import Mock

from common.errors import LogError, SemanticError, StorageError
from common.interfaces import ConcurrencyControlManager, FailureRecoveryManager, QueryOptimizer, Session
from common.log import LogResult, RecoveryResult, RecoveryTarget
from common.result import ExecutionResult
from common.transaction import TxResult, TxStatus
from query_processor.ddl import DDLCoordinator
from query_processor.errors import PlanError
from query_processor.facade import MetaCommandHandler
from query_processor.transactions import TransactionCoordinator


class TestDDLCoordinator(unittest.TestCase):
    def setUp(self) -> None:
        self.ccm = Mock(spec=ConcurrencyControlManager)
        self.ccm.begin_transaction.side_effect = [1, 2, 3]
        self.ccm.commit.side_effect = lambda tx: TxResult(tx, TxStatus.COMMITTED, True)
        self.ccm.abort.side_effect = lambda tx: TxResult(tx, TxStatus.ABORTED, True)
        self.tx = TransactionCoordinator(self.ccm)
        self.session = Session(1)

    def test_default_policy_rejects_ddl_before_running_work(self) -> None:
        coordinator = DDLCoordinator(self.tx)
        work = Mock(return_value=ExecutionResult(ok=True, message="CREATE TABLE"))

        result = coordinator.run(self.session, work)

        self.assertFalse(result.ok)
        self.assertIn("DDL", result.message)
        work.assert_not_called()
        self.ccm.begin_transaction.assert_not_called()
        self.assertIsNone(self.session.tx_id)

    def test_rejects_none_session_and_active_explicit_transaction(self) -> None:
        coordinator = DDLCoordinator(self.tx, allow_ddl=True)
        work = Mock(return_value=ExecutionResult(ok=True, message="OK"))

        none_res = coordinator.run(None, work)
        self.assertFalse(none_res.ok)
        work.assert_not_called()

        self.assertTrue(self.tx.begin(self.session).ok)
        active_res = coordinator.run(self.session, work)
        self.assertFalse(active_res.ok)
        self.assertIn("transaksi aktif", active_res.message)
        work.assert_not_called()
        self.assertEqual(self.session.tx_id, 1)
        self.assertTrue(self.session.in_explicit_tx)

    def test_rejects_session_with_uncertain_commit_or_pending_abort(self) -> None:
        coordinator = DDLCoordinator(self.tx, allow_ddl=True)
        work = Mock(return_value=ExecutionResult(ok=True, message="OK"))

        self.ccm.commit.side_effect = lambda tx: TxResult(tx, TxStatus.FAILED, False, "commit belum pasti")
        autocommit_res = self.tx.run_autocommit(self.session, lambda tx: ExecutionResult(ok=True))
        self.assertFalse(autocommit_res.ok)
        self.assertEqual(self.session.tx_id, 1)

        ddl_res = coordinator.run(self.session, work)
        self.assertFalse(ddl_res.ok)
        self.assertIn("belum pasti", ddl_res.message)
        work.assert_not_called()

        session2 = Session(2)
        self.assertTrue(self.tx.begin(session2).ok)
        self.ccm.abort.side_effect = lambda tx: TxResult(tx, TxStatus.FAILED, False, "undo gagal")
        self.assertFalse(self.tx.abort(session2).ok)

        pending_res = coordinator.run(session2, work)
        self.assertFalse(pending_res.ok)
        self.assertIn("Rollback", pending_res.message)
        work.assert_not_called()

    def test_enabled_coordinator_executes_without_ccm_transaction_and_normalizes_result(self) -> None:
        coordinator = DDLCoordinator(self.tx, allow_ddl=True)
        result = coordinator.run(
            self.session,
            lambda: ExecutionResult(ok=True, affected_rows=5, message="Tabel dibuat"),
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.affected_rows, 0)
        self.assertEqual(result.message, "Tabel dibuat")
        self.ccm.begin_transaction.assert_not_called()
        self.ccm.commit.assert_not_called()
        self.ccm.abort.assert_not_called()
        self.assertIsNone(self.session.tx_id)

    def test_domain_and_unexpected_errors_convert_to_failed_execution_result(self) -> None:
        coordinator = DDLCoordinator(self.tx, allow_ddl=True)
        for error, expected_sub in (
            (PlanError("Plan tidak valid: skema kosong"), "Plan tidak valid"),
            (SemanticError("Tabel masih dirujuk"), "SEMANTIC"),
            (StorageError("Gagal menulis katalog"), "STORAGE"),
            (LogError("Log rusak"), "LOG"),
        ):
            with self.subTest(error=error):
                res = coordinator.run(self.session, Mock(side_effect=error))
                self.assertFalse(res.ok)
                self.assertIn(expected_sub, res.message)
                self.assertIsNone(self.session.tx_id)

        with self.assertLogs("coachdb.qp", level="ERROR"):
            unexpected = coordinator.run(
                self.session,
                Mock(side_effect=RuntimeError("kesalahan tak terduga")),
            )
        self.assertFalse(unexpected.ok)
        self.assertIn("INTERNAL", unexpected.message)
        self.assertIsNone(self.session.tx_id)

    def test_admin_lock_serializes_concurrent_ddl_and_checkpoint_restore(self) -> None:
        coordinator = DDLCoordinator(self.tx, allow_ddl=True)
        qo = Mock(spec=QueryOptimizer)
        frm = Mock(spec=FailureRecoveryManager)
        meta = MetaCommandHandler(qo=qo, frm=frm, ccm=self.ccm, admin_lock=coordinator.admin_lock)

        entered = threading.Event()
        release = threading.Event()
        order: list[str] = []

        def slow_ddl() -> ExecutionResult:
            order.append("ddl_start")
            entered.set()
            release.wait(timeout=2.0)
            order.append("ddl_end")
            return ExecutionResult(ok=True, message="DDL selesai")

        def checkpoint_call() -> LogResult:
            order.append("checkpoint")
            return LogResult(ok=True, lsn=10, message="CHECKPOINT selesai")

        def restore_call(target: RecoveryTarget) -> RecoveryResult:
            order.append("restore")
            return RecoveryResult(ok=True, last_tx_id=4, last_lsn=12, message="RESTORE selesai")

        frm.checkpoint.side_effect = checkpoint_call
        frm.recover_to.side_effect = restore_call

        t1 = threading.Thread(target=lambda: coordinator.run(Session(1), slow_ddl))
        t1.start()
        self.assertTrue(entered.wait(timeout=2.0))

        t2 = threading.Thread(target=lambda: meta.checkpoint(Session(2)))
        t3 = threading.Thread(target=lambda: meta.restore(RecoveryTarget(tx_id=4), Session(3)))
        t2.start()
        t3.start()

        release.set()
        t1.join(timeout=2.0)
        t2.join(timeout=2.0)
        t3.join(timeout=2.0)

        self.assertEqual(order[:2], ["ddl_start", "ddl_end"])
        self.assertCountEqual(order[2:], ["checkpoint", "restore"])


if __name__ == "__main__":
    unittest.main()
