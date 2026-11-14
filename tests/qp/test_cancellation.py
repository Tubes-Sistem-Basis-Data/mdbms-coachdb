from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event
import unittest
from unittest.mock import Mock, patch

from common.catalog import DataType
from common.errors import TransactionAbortedError
from common.expression import ColumnRef, Subquery
from common.interfaces import ConcurrencyControlManager, QueryOptimizer, StorageManager
from common.plan import ParsedQuery, Project, QueryKind, TableScan
from common.result import ExecutionResult
from common.storage import RID
from common.transaction import AccessResponse, AccessStatus, TxResult, TxStatus
from query_processor.cancellation import cancellation_scope, current_cancel_check
from query_processor.context import ExecContext
from query_processor.errors import QueryCancelledError
from query_processor.facade import QueryProcessorFacade
from query_processor.operators.base import Executable
from query_processor.operators.insert import InsertExecution
from query_processor.translator import PlanTranslator


class TestCancellationScope(unittest.TestCase):
    def test_scope_resets_after_failure_and_does_not_leak_to_another_thread(self) -> None:
        self.assertIsNone(current_cancel_check())
        with self.assertRaises(RuntimeError):
            with cancellation_scope(lambda: True):
                self.assertTrue(current_cancel_check()())
                with ThreadPoolExecutor(max_workers=1) as pool:
                    self.assertIsNone(pool.submit(current_cancel_check).result())
                raise RuntimeError("request gagal")
        self.assertIsNone(current_cancel_check())

    def test_context_captures_its_own_request_signal(self) -> None:
        cancelled = Event()
        with cancellation_scope(cancelled.is_set):
            first = ExecContext(7, None, None)
        with cancellation_scope(lambda: False):
            second = ExecContext(8, None, None)
        cancelled.set()
        with self.assertRaises(QueryCancelledError):
            first.check_cancelled()
        second.check_cancelled()
        ExecContext(9, None, None).check_cancelled()


class TestContextCancellation(unittest.TestCase):
    def setUp(self) -> None:
        self.cancelled = Event()
        self.sm = Mock(spec=StorageManager)
        self.ccm = Mock(spec=ConcurrencyControlManager)
        self.ctx = ExecContext(7, self.sm, self.ccm, cancel_check=self.cancelled.is_set)

    def test_cancelled_request_never_asks_ccm_for_access(self) -> None:
        self.cancelled.set()
        with self.assertRaises(QueryCancelledError):
            self.ctx.authorize_table("student")
        self.ccm.validate_object.assert_not_called()
        self.assertEqual(self.sm.mock_calls, [])

    @patch("query_processor.context.sleep")
    def test_wait_retry_stops_when_request_is_cancelled(self, sleep: Mock) -> None:
        self.ccm.validate_object.return_value = AccessResponse(AccessStatus.WAIT)
        sleep.side_effect = lambda delay: self.cancelled.set()
        with self.assertRaises(QueryCancelledError):
            self.ctx.authorize_table("student")
        sleep.assert_called_once()
        self.ccm.validate_object.assert_called_once()
        self.ccm.abort.assert_not_called()

    @patch("query_processor.context.sleep")
    def test_cancel_during_validation_stops_before_sleep(self, sleep: Mock) -> None:
        def wait(*args):
            self.cancelled.set()
            return AccessResponse(AccessStatus.WAIT)
        self.ccm.validate_object.side_effect = wait
        with self.assertRaises(QueryCancelledError):
            self.ctx.authorize_table("student")
        sleep.assert_not_called()
        self.ccm.validate_object.assert_called_once()

    def test_cancel_after_write_permission_never_writes_and_releases_reserved_rid(self) -> None:
        rid = RID("student", 0, 2)
        self.sm.validate.side_effect = lambda table, values: values
        self.sm.allocate_rid.return_value = rid
        def allow(*args):
            self.cancelled.set()
            return AccessResponse(AccessStatus.ALLOWED, lsn=12)
        self.ccm.validate_object.side_effect = allow

        with self.assertRaises(QueryCancelledError) as raised:
            InsertExecution("student", (3, "new")).execute(self.ctx)

        self.assertNotIsInstance(raised.exception, TransactionAbortedError)
        self.sm.insert.assert_not_called()
        self.sm.release_rid.assert_called_once_with(rid)
        self.ccm.abort.assert_not_called()

    def test_ccm_aborted_response_is_not_converted_to_cancellation(self) -> None:
        def aborted(*args):
            self.cancelled.set()
            return AccessResponse(AccessStatus.ABORTED, message="deadlock")
        self.ccm.validate_object.side_effect = aborted
        with self.assertRaises(TransactionAbortedError):
            self.ctx.authorize_table("student")
        self.ccm.abort.assert_not_called()

    def test_cancelled_statement_does_not_reuse_cached_scalar(self) -> None:
        scalar = Subquery(Project(columns=[ColumnRef("student", "id")],
                                  children=[TableScan(table="student")]))
        resolver = Mock(return_value=(1, DataType.INTEGER))
        self.ctx.scalar_resolver = resolver
        self.ctx.resolve_scalar(scalar)
        self.cancelled.set()
        with self.assertRaises(QueryCancelledError):
            self.ctx.resolve_scalar(scalar)
        resolver.assert_called_once_with(scalar)


class TestFacadeCancellation(unittest.TestCase):
    def setUp(self) -> None:
        self.cancelled = Event()
        self.ccm = Mock(spec=ConcurrencyControlManager)
        self.ccm.begin_transaction.return_value = 7
        self.ccm.abort.return_value = TxResult(7, TxStatus.ABORTED, True)
        qo = Mock(spec=QueryOptimizer)
        qo.parse_query.return_value = ParsedQuery("manual plan", QueryKind.SELECT,
            Project(columns=[ColumnRef("student", "id")], children=[TableScan(table="student")]))
        qo.optimize_query.side_effect = lambda parsed: parsed
        translator = Mock(spec=PlanTranslator)
        executable = Mock(spec=Executable)
        def finish_work(ctx):
            self.cancelled.set()
            return ExecutionResult(ok=True)
        executable.execute.side_effect = finish_work
        translator.build.return_value = executable
        self.qp = QueryProcessorFacade(qo, Mock(spec=StorageManager), self.ccm, translator=translator)
        self.session = self.qp.open_session()

    def test_cancel_after_work_rolls_back_instead_of_autocommitting(self) -> None:
        with cancellation_scope(self.cancelled.is_set):
            result = self.qp.execute_query("SELECT id FROM student;", self.session)
        self.assertFalse(result.ok)
        self.assertIn("dibatalkan", result.message)
        self.ccm.abort.assert_called_once_with(7)
        self.ccm.commit.assert_not_called()
        self.assertIsNone(self.session.tx_id)

    def test_failed_cancel_rollback_keeps_transaction_for_retry(self) -> None:
        self.ccm.abort.return_value = TxResult(7, TxStatus.FAILED, False, "undo gagal")
        with cancellation_scope(self.cancelled.is_set):
            result = self.qp.execute_query("SELECT id FROM student;", self.session)
        self.assertFalse(result.ok)
        self.assertIn("undo gagal", result.message)
        self.assertEqual(self.session.tx_id, 7)
        self.assertFalse(self.qp.execute_query("SELECT id FROM student;", self.session).ok)
        self.ccm.abort.assert_called_once_with(7)
        self.ccm.commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
