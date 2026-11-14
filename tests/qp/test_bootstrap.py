from __future__ import annotations

from contextlib import ExitStack, redirect_stderr, redirect_stdout
import io
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import Mock, patch

import main
from common.errors import LogError, MdbmsError, StorageError
from common.interfaces import ConcurrencyControlManager, FailureRecoveryManager, QueryOptimizer, StorageManager
from common.log import RecoveryResult
from common.transaction import ProtocolType, TxResult, TxStatus
from query_processor.errors import ExecutionError
from query_processor.facade import QueryProcessorFacade
from query_processor.server import QueryServer


class TestBootstrap(unittest.TestCase):
    def setUp(self) -> None:
        self.sm = Mock(spec=StorageManager)
        self.sm.close = Mock()
        self.frm = Mock(spec=FailureRecoveryManager)
        self.frm.close = Mock()
        self.frm.recover.return_value = RecoveryResult(ok=True, last_tx_id=41)
        self.ccm = Mock(spec=ConcurrencyControlManager)
        self.ccm.get_protocol.return_value = ProtocolType.LOCK_BASED
        self.qo = Mock(spec=QueryOptimizer)

    def factories(self):
        stack = ExitStack()
        stack.enter_context(patch("main.StorageManager", return_value=self.sm))
        stack.enter_context(patch("main.CCManager", return_value=self.ccm))
        stack.enter_context(patch("main.QueryOptimizerFacade", return_value=self.qo))
        self.frm_factory = stack.enter_context(patch("main.FailureRecoveryManager", return_value=self.frm))
        stack.enter_context(redirect_stdout(io.StringIO()))
        return stack

    def test_data_directory_controls_wal_and_recovery_precedes_transaction_initialization(self) -> None:
        calls = Mock()
        calls.attach_mock(self.frm, "frm")
        calls.attach_mock(self.ccm, "ccm")
        with self.factories():
            qp = main.bootstrap(main.parse_args(["--data-dir", "database with spaces"]))
        self.frm_factory.assert_called_once_with(log_path=str(Path("database with spaces") / "wal.log"))
        self.frm.set_dependencies.assert_called_once_with(self.sm, self.ccm)
        self.sm.set_log_flusher.assert_called_once_with(self.frm.flush_log)
        self.ccm.initialize.assert_called_once_with(41)
        names = [call[0] for call in calls.mock_calls]
        self.assertLess(names.index("frm.recover"), names.index("ccm.initialize"))
        self.assertIsInstance(qp, QueryProcessorFacade)

    def test_recovery_failure_closes_resources_without_accepting_transactions(self) -> None:
        self.frm.recover.return_value = RecoveryResult(ok=False, message="recovery gagal")
        with self.factories(), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            main.bootstrap(main.parse_args([]))
        self.assertEqual(raised.exception.code, 1)
        self.sm.close.assert_called_once()
        self.frm.close.assert_called_once()
        self.ccm.initialize.assert_not_called()
        self.ccm.begin_transaction.assert_not_called()

    def test_cleanup_failure_does_not_hide_the_startup_error(self) -> None:
        self.frm.recover.side_effect = LogError("startup gagal")
        self.sm.close.side_effect = StorageError("close gagal")
        with self.factories(), self.assertRaisesRegex(LogError, "startup gagal"):
            main.bootstrap(main.parse_args([]))
        self.frm.close.assert_called_once()

    def test_real_bootstrap_keeps_each_databases_recovery_files_in_its_directory(self) -> None:
        from failure_recovery.log import codec
        previous_path = codec.get_log_path()
        self.addCleanup(codec.set_log_path, previous_path)
        with TemporaryDirectory() as tmp:
            for name in ("first db", "second db"):
                directory = Path(tmp) / name
                with redirect_stdout(io.StringIO()):
                    qp = main.bootstrap(main.parse_args(["--data-dir", str(directory)]))
                try:
                    self.assertEqual(qp._frm._log_path, directory / "wal.log")
                    self.assertEqual(Path(qp._frm._master_path).parent, directory)
                    self.assertEqual(Path(qp._frm._snapshot_dir).parent, directory)
                    self.assertTrue((directory / "wal.log").exists())
                finally:
                    qp.close()
                    qp._sm._disk.close()

    def test_cli_failure_still_closes_the_runtime(self) -> None:
        qp = Mock()
        with patch("main.bootstrap", return_value=qp), \
             patch("main.ClientCLI") as client:
            client.return_value.run.side_effect = RuntimeError("CLI gagal")
            with self.assertRaisesRegex(RuntimeError, "CLI gagal"):
                main.main(["--cli"])
        qp.close.assert_called_once()

    def test_server_shutdown_waits_for_handlers_before_closing_the_runtime(self) -> None:
        events = []
        qp = Mock()
        qp.close.side_effect = lambda: events.append("close")
        server = Mock()
        server.serve.side_effect = KeyboardInterrupt()
        server.shutdown.side_effect = lambda: events.append("shutdown")
        server.wait_closed.side_effect = lambda: events.append("wait")
        with patch("main.bootstrap", return_value=qp), patch("main.QueryServer", return_value=server), \
             redirect_stdout(io.StringIO()):
            main.main(["--serve"])
        self.assertEqual(events, ["shutdown", "wait", "close"])

    def test_unfinished_server_handler_prevents_backend_close(self) -> None:
        qp = Mock()
        server = Mock()
        server.wait_closed.side_effect = ExecutionError("handler aktif")
        with patch("main.bootstrap", return_value=qp), patch("main.QueryServer", return_value=server), \
             redirect_stdout(io.StringIO()), self.assertRaises(ExecutionError):
            main.main(["--serve"])
        qp.close.assert_not_called()


class TestRuntimeClose(unittest.TestCase):
    def setUp(self) -> None:
        self.sm = Mock(spec=StorageManager)
        self.sm.close = Mock()
        self.frm = Mock(spec=FailureRecoveryManager)
        self.frm.close = Mock()
        self.ccm = Mock(spec=ConcurrencyControlManager)
        self.ccm.begin_transaction.return_value = 7
        self.ccm.abort.side_effect = lambda tx: TxResult(tx, TxStatus.ABORTED, True)
        self.qp = QueryProcessorFacade(Mock(spec=QueryOptimizer), self.sm, self.ccm, self.frm)
        self.session = self.qp.open_session()
        self.qp._transactions.begin(self.session)

    def test_close_aborts_sessions_then_flushes_storage_before_closing_wal(self) -> None:
        calls = Mock()
        for component, name in ((self.ccm, "ccm"), (self.sm, "sm"), (self.frm, "frm")):
            calls.attach_mock(component, name)
        self.qp.close()
        self.assertEqual([call[0] for call in calls.mock_calls],
                         ["ccm.abort", "sm.flush", "sm.close", "frm.close"])
        self.assertIsNone(self.session.tx_id)
        self.qp.close()
        self.sm.close.assert_called_once()
        self.frm.close.assert_called_once()
        with self.assertRaises(MdbmsError):
            self.qp.open_session()
        self.assertFalse(self.qp.execute_query("SELECT * FROM t;", self.session).ok)

    def test_failed_undo_keeps_runtime_open_for_cleanup_retry(self) -> None:
        self.ccm.abort.side_effect = None
        self.ccm.abort.return_value = TxResult(7, TxStatus.FAILED, False, "undo gagal")
        with self.assertLogs("coachdb.qp", level="ERROR"), self.assertRaises(MdbmsError):
            self.qp.close()
        self.assertEqual(self.session.tx_id, 7)
        self.sm.flush.assert_not_called()
        self.frm.close.assert_not_called()
        self.ccm.abort.return_value = TxResult(7, TxStatus.ABORTED, True)
        self.qp.close()
        self.assertIsNone(self.session.tx_id)
        self.frm.close.assert_called_once()

    def test_flush_failure_does_not_close_wal_or_report_runtime_closed(self) -> None:
        self.sm.flush.side_effect = StorageError("flush gagal")
        with self.assertRaisesRegex(StorageError, "flush gagal"):
            self.qp.close()
        self.frm.close.assert_not_called()
        self.sm.close.assert_not_called()
        self.sm.flush.side_effect = None
        self.qp.close()
        self.frm.close.assert_called_once()

    def test_unknown_commit_is_not_aborted_or_closed_during_shutdown(self) -> None:
        self.ccm.commit.return_value = TxResult(7, TxStatus.FAILED, False, "commit belum pasti")
        self.qp._transactions.commit(self.session)
        with self.assertLogs("coachdb.qp", level="ERROR"), self.assertRaises(MdbmsError):
            self.qp.close()
        self.assertEqual(self.session.tx_id, 7)
        self.ccm.abort.assert_not_called()
        self.sm.flush.assert_not_called()
        self.frm.close.assert_not_called()

    def test_recovery_close_retry_does_not_reopen_or_flush_closed_storage(self) -> None:
        self.frm.close.side_effect = LogError("close WAL gagal")
        with self.assertRaises(LogError):
            self.qp.close()
        self.frm.close.side_effect = None
        self.qp.close()
        self.sm.flush.assert_called_once()
        self.sm.close.assert_called_once()
        self.assertEqual(self.frm.close.call_count, 2)

    def test_wait_closed_rejects_a_handler_that_has_not_stopped(self) -> None:
        server = QueryServer(self.qp)
        thread = Mock(spec=Thread)
        thread.is_alive.return_value = True
        server._client_threads.append(thread)
        with self.assertRaises(ExecutionError):
            server.wait_closed(timeout=0)
        thread.join.assert_called_once_with(timeout=0.0)


if __name__ == "__main__":
    unittest.main()
