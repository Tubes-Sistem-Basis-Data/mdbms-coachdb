from __future__ import annotations

import unittest
from unittest.mock import Mock

from common.errors import LogError, StorageError, TransactionAbortedError
from common.interfaces import ConcurrencyControlManager, Session
from common.result import ExecutionResult
from common.transaction import TxResult, TxStatus
from query_processor.transactions import TransactionCoordinator


class TestTransactionCoordinator(unittest.TestCase):
    def setUp(self):
        self.ccm = Mock(spec=ConcurrencyControlManager)
        self.ccm.begin_transaction.side_effect = [11, 12, 13]
        self.ccm.commit.side_effect = lambda tx: TxResult(tx, TxStatus.COMMITTED, True)
        self.ccm.abort.side_effect = lambda tx: TxResult(tx, TxStatus.ABORTED, True)
        self.coordinator = TransactionCoordinator(self.ccm)
        self.session = Session(1)

    def assert_idle(self):
        self.assertIsNone(self.session.tx_id)
        self.assertFalse(self.session.in_explicit_tx)

    def test_explicit_begin_commit_and_abort(self):
        self.assertTrue(self.coordinator.begin(self.session).ok)
        self.assertEqual(self.session.tx_id, 11)
        self.assertTrue(self.session.in_explicit_tx)
        self.assertFalse(self.coordinator.begin(self.session).ok)
        self.ccm.begin_transaction.assert_called_once()
        self.assertTrue(self.coordinator.commit(self.session).ok)
        self.ccm.commit.assert_called_once_with(11)
        self.assert_idle()
        self.coordinator.begin(self.session)
        self.assertTrue(self.coordinator.abort(self.session).ok)
        self.ccm.abort.assert_called_once_with(12)
        self.assert_idle()

    def test_finish_without_transaction(self):
        for method in (self.coordinator.commit, self.coordinator.abort):
            result = method(self.session)
            self.assertFalse(result.ok)
            self.assertIn("Tidak ada transaksi", result.message)
        self.ccm.commit.assert_not_called()
        self.ccm.abort.assert_not_called()

    def test_autocommit_preserves_query_result_and_orders_calls(self):
        result = ExecutionResult(ok=True, columns=["id"], rows=[[1]])
        def work(tx):
            self.assertEqual(tx, self.session.tx_id)
            self.assertFalse(self.session.in_explicit_tx)
            self.ccm.commit.assert_not_called()
            return result
        self.assertIs(self.coordinator.run_autocommit(self.session, work), result)
        self.ccm.commit.assert_called_once_with(11)
        self.assert_idle()

    def test_autocommit_rejects_existing_transaction(self):
        self.coordinator.begin(self.session)
        work = Mock()
        self.assertFalse(self.coordinator.run_autocommit(self.session, work).ok)
        work.assert_not_called()
        self.assertEqual(self.session.tx_id, 11)
        self.ccm.commit.assert_not_called()

    def test_failed_query_result_rolls_back(self):
        failure = ExecutionResult(ok=False, message="operasi gagal")
        self.assertIs(self.coordinator.run_autocommit(self.session, lambda tx: failure), failure)
        self.ccm.abort.assert_called_once_with(11)
        self.ccm.commit.assert_not_called()
        self.assert_idle()

    def test_runtime_exception_rolls_back(self):
        result = self.coordinator.run_autocommit(self.session, Mock(side_effect=StorageError("disk gagal")))
        self.assertFalse(result.ok)
        self.assertIn("disk gagal", result.message)
        self.ccm.abort.assert_called_once_with(11)
        self.assert_idle()

    def test_ccm_aborted_does_not_repeat_abort(self):
        result = self.coordinator.run_autocommit(
            self.session, Mock(side_effect=TransactionAbortedError("deadlock", 11)))
        self.assertFalse(result.ok)
        self.assertIn("deadlock", result.message)
        self.ccm.abort.assert_not_called()
        self.ccm.commit.assert_not_called()
        self.assert_idle()

    def test_uncertain_commit_preserves_transaction_without_abort(self):
        self.ccm.commit.side_effect = lambda tx: TxResult(tx, TxStatus.FAILED, False, "commit tidak pasti")
        result = self.coordinator.run_autocommit(self.session, lambda tx: ExecutionResult(ok=True))
        self.assertFalse(result.ok)
        self.assertEqual(result.message, "commit tidak pasti")
        self.assertEqual(self.session.tx_id, 11)
        self.ccm.abort.assert_not_called()

    def test_rejected_commit_with_terminal_abort_clears_session(self):
        self.coordinator.begin(self.session)
        self.ccm.commit.side_effect = lambda tx: TxResult(tx, TxStatus.ABORTED, False)
        self.assertFalse(self.coordinator.commit(self.session).ok)
        self.assert_idle()

    def test_abort_failure_preserves_session(self):
        self.coordinator.begin(self.session)
        self.ccm.abort.side_effect = lambda tx: TxResult(tx, TxStatus.FAILED, False, "undo gagal")
        self.assertFalse(self.coordinator.abort(self.session).ok)
        self.assertEqual(self.session.tx_id, 11)
        self.assertTrue(self.session.in_explicit_tx)

    def test_pending_rollback_blocks_work_until_abort_retry_succeeds(self):
        self.coordinator.begin(self.session)
        self.ccm.abort.side_effect = lambda tx: TxResult(tx, TxStatus.FAILED, False, "undo gagal")
        self.assertFalse(self.coordinator.abort(self.session).ok)

        work = Mock()
        for operation in (
            lambda: self.coordinator.begin(self.session),
            lambda: self.coordinator.commit(self.session),
            lambda: self.coordinator.run_autocommit(self.session, work),
        ):
            result = operation()
            self.assertFalse(result.ok)
            self.assertIn("Rollback", result.message)
        work.assert_not_called()
        self.ccm.commit.assert_not_called()
        self.ccm.abort.assert_called_once_with(11)
        self.assertTrue(self.coordinator.begin(Session(2)).ok)

        self.ccm.abort.side_effect = lambda tx: TxResult(tx, TxStatus.ABORTED, True)
        self.assertTrue(self.coordinator.abort(self.session).ok)
        self.assert_idle()
        self.assertIsNone(self.coordinator.blocked_result(self.session))
        self.assertTrue(self.coordinator.begin(self.session).ok)

    def test_invalid_abort_response_preserves_and_blocks_transaction(self):
        self.coordinator.begin(self.session)
        for response in (None, TxResult(99, TxStatus.ABORTED, True), TxResult(11, "ABORTED", True)):
            with self.subTest(response=response):
                self.ccm.abort.reset_mock()
                self.ccm.abort.side_effect = None
                self.ccm.abort.return_value = response
                result = self.coordinator.abort(self.session)
                self.assertFalse(result.ok)
                self.assertEqual(self.session.tx_id, 11)
                self.assertIsNotNone(self.coordinator.blocked_result(self.session))
                self.ccm.abort.assert_called_once_with(11)

    def test_begin_and_commit_log_errors(self):
        self.ccm.begin_transaction.side_effect = LogError("begin gagal")
        self.assertFalse(self.coordinator.begin(self.session).ok)
        self.assert_idle()
        self.session.tx_id = 99
        self.session.in_explicit_tx = True
        self.ccm.commit.side_effect = LogError("commit gagal")
        self.assertFalse(self.coordinator.commit(self.session).ok)
        self.assertEqual(self.session.tx_id, 99)
        self.ccm.abort.assert_not_called()

    def test_disconnect_and_runtime_cleanup(self):
        self.coordinator.begin(self.session)
        self.coordinator.abort_on_disconnect(self.session)
        self.coordinator.abort_on_disconnect(self.session)
        self.ccm.abort.assert_called_once_with(11)
        self.assert_idle()
        self.coordinator.begin(self.session)
        self.coordinator.handle_runtime_error(self.session, StorageError("gagal"))
        self.assert_idle()
        self.assertEqual(self.ccm.abort.call_count, 2)

    def test_rollback_failure_reports_both_errors(self):
        self.ccm.abort.side_effect = LogError("undo gagal")
        result = self.coordinator.run_autocommit(self.session, Mock(side_effect=StorageError("write gagal")))
        self.assertFalse(result.ok)
        self.assertIn("write gagal", result.message)
        self.assertIn("undo gagal", result.message)
        self.assertEqual(self.session.tx_id, 11)

    def test_sessions_are_independent(self):
        other = Session(2)
        self.coordinator.begin(self.session)
        self.coordinator.begin(other)
        self.coordinator.abort(self.session)
        self.assertEqual(other.tx_id, 12)
        self.assertTrue(other.in_explicit_tx)
        self.coordinator.commit(other)
        self.ccm.commit.assert_called_once_with(12)


if __name__ == "__main__":
    unittest.main()
