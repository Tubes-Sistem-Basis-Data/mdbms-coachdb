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

    def test_demo_runs_from_empty_data_dir_with_enroll_query(self) -> None:
        from failure_recovery.log import codec

        self.assertIn("EXPLAIN SELECT * FROM enroll;", main.DEMO_QUERIES)
        self.assertNotIn("EXPLAIN SELECT * FROM enrollment;", main.DEMO_QUERIES)

        previous_path = codec.get_log_path()
        self.addCleanup(codec.set_log_path, previous_path)
        with TemporaryDirectory() as tmp:
            output = io.StringIO()
            with redirect_stdout(output):
                main.main(["--demo", "--data-dir", tmp])
            text = output.getvalue()
            self.assertIn("> SELECT * FROM student;", text)
            self.assertIn("(60 baris)", text)
            self.assertIn("> SELECT * FROM course;", text)
            self.assertIn("(50 baris)", text)
            self.assertIn("> EXPLAIN SELECT * FROM enroll;", text)
            self.assertIn("TableScan [enroll]", text)
            self.assertIn("> SELECT * FROM dosen;", text)
            self.assertIn("ERROR [SEMANTIC]", text)

    def test_two_clients_via_main_serve(self) -> None:
        import sys
        from time import monotonic, sleep
        from client import ClientCLI
        from failure_recovery.log import codec

        original_stdout = sys.stdout
        previous_path = codec.get_log_path()
        self.addCleanup(codec.set_log_path, previous_path)
        with TemporaryDirectory() as tmp:
            with redirect_stdout(io.StringIO()):
                qp_init = main.bootstrap(main.parse_args(["--data-dir", tmp]))
                try:
                    main._ensure_demo_data(qp_init)
                finally:
                    main.close_runtime(qp_init)

            servers: list[QueryServer] = []

            def make_server(*args, **kwargs) -> QueryServer:
                srv = QueryServer(*args, **kwargs)
                servers.append(srv)
                return srv

            server_output = io.StringIO()

            def run_server() -> None:
                with redirect_stdout(server_output):
                    main.main(["--serve", "--data-dir", tmp, "--host", "127.0.0.1", "--port", "0"])

            with patch("main.QueryServer", side_effect=make_server):
                thread = Thread(target=run_server, daemon=True)
                client1: ClientCLI | None = None
                client2: ClientCLI | None = None
                thread.start()
                try:
                    deadline = monotonic() + 5.0
                    while (not servers or not servers[0]._running or servers[0].port == 0) and monotonic() < deadline:
                        sleep(0.02)

                    self.assertTrue(servers and servers[0]._running)
                    port = servers[0].port

                    client1 = ClientCLI(host="127.0.0.1", port=port)
                    client2 = ClientCLI(host="127.0.0.1", port=port)

                    res1 = client1.send("SELECT id, name FROM student WHERE id = 1;")
                    res2 = client2.send("SELECT id, name FROM course WHERE id = 1;")
                    self.assertTrue(res1.ok, res1.message)
                    self.assertTrue(res2.ok, res2.message)
                    self.assertEqual(len(res1.rows), 1)
                    self.assertEqual(len(res2.rows), 1)
                    self.assertIsNotNone(client1._session_id)
                    self.assertIsNotNone(client2._session_id)
                    self.assertNotEqual(client1._session_id, client2._session_id)

                    self.assertTrue(client1.send("BEGIN;").ok)
                    ins = client1.send(
                        "INSERT INTO student VALUES (999, 'Mahasiswa Baru', 'CS', 3.95);"
                    )
                    self.assertTrue(ins.ok, ins.message)
                    self.assertEqual(ins.affected_rows, 1)
                    self.assertTrue(client1.send("COMMIT;").ok)

                    check2 = client2.send("SELECT name FROM student WHERE id = 999;")
                    self.assertTrue(check2.ok, check2.message)
                    self.assertEqual(check2.rows, [["Mahasiswa Baru"]])
                finally:
                    if client1 is not None:
                        client1.close()
                    if client2 is not None:
                        client2.close()
                    for srv in servers:
                        srv.shutdown()
                    thread.join(timeout=5.0)
                    self.assertFalse(thread.is_alive())

        self.assertIs(sys.stdout, original_stdout)


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
