from __future__ import annotations

from contextlib import suppress
import json
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
from threading import Event, Thread
from time import monotonic, sleep
import unittest
from unittest.mock import patch

from client import ClientCLI
from common.catalog import ColumnSchema, DataType
from common.transaction import AccessStatus, TxResult, TxStatus
from concurrency_control import CCManager
from failure_recovery import FailureRecoveryManager
from failure_recovery.log import codec
from query_optimizer import QueryOptimizerFacade
from query_processor.facade import QueryProcessorFacade
from query_processor.server import QueryServer
from storage_manager.facade import StorageManager


class TestDisconnectDuringWait(unittest.TestCase):
    def setUp(self) -> None:
        previous_path = codec.get_log_path()
        self.addCleanup(codec.set_log_path, previous_path)
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.sm = StorageManager(data_dir=temp.name)
        self.addCleanup(self.sm._disk.close)
        for table in ("student", "blocked"):
            self.sm._catalog.create_table(table, (
                ColumnSchema("id", DataType.INTEGER, nullable=False),
                ColumnSchema("name", DataType.VARCHAR, length=50),
            ), ("id",), ())
        self.original = [[1, "old"], [2, "two"]]
        self.sm.bulk_load("student", self.original)
        self.sm.bulk_load("blocked", [[1, "old"]])
        self.frm = FailureRecoveryManager(log_path=str(Path(temp.name) / "wal.log"))
        self.addCleanup(self.frm.close)
        self.ccm = CCManager(self.frm)
        self.frm.set_dependencies(self.sm, self.ccm)
        self.sm.set_log_flusher(self.frm.flush_log)
        self.qp = QueryProcessorFacade(QueryOptimizerFacade(self.sm), self.sm, self.ccm, self.frm)
        self.server = QueryServer(self.qp, port=0)
        self.clients: list[ClientCLI] = []
        self.sockets: list[socket.socket] = []
        self.query_threads: list[Thread] = []
        self.server_thread = Thread(target=self.server.serve, daemon=True)
        self.addCleanup(self.stop_transport)
        self.server_thread.start()
        self.wait_until(lambda: self.server.port != 0, "server belum mendengarkan")

    def stop_transport(self) -> None:
        self.server.shutdown()
        for conn in self.sockets:
            with suppress(OSError):
                conn.shutdown(socket.SHUT_RDWR)
        for thread in self.query_threads:
            thread.join(timeout=2)
        for client in self.clients:
            client.close()
        for conn in self.sockets:
            conn.close()
        self.server_thread.join(timeout=2)
        for thread in self.server._client_threads:
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive(), "handler client belum berhenti")

    def wait_until(self, condition, message: str) -> None:
        deadline = monotonic() + 2
        while monotonic() < deadline:
            if condition():
                return
            sleep(0.01)
        self.fail(message)

    def client(self) -> ClientCLI:
        client = ClientCLI(port=self.server.port)
        client.connect()
        client._connection.settimeout(3)
        self.clients.append(client)
        self.sockets.append(client._connection)
        return client

    def execute_ok(self, client: ClientCLI, sql: str):
        result = client.send(sql)
        self.assertTrue(result.ok, result.message)
        return result

    def start_query(self, client: ClientCLI, sql: str):
        responses = []
        thread = Thread(target=lambda: responses.append(client.send(sql)), daemon=True)
        self.query_threads.append(thread)
        thread.start()
        return thread, responses

    def observe_wait(self, waiting: Event):
        validate = self.ccm.validate_object
        def record(*args, **kwargs):
            response = validate(*args, **kwargs)
            if response.status is AccessStatus.WAIT:
                self.waiting_tx_id = args[0]
                waiting.set()
            return response
        return patch.object(self.ccm, "validate_object", side_effect=record)

    def session_for_tx(self, tx_id: int):
        return next(session for session in self.qp._sessions.active_sessions() if session.tx_id == tx_id)

    def test_disconnect_while_waiting_undoes_prior_writes_without_releasing_other_clients_lock(self) -> None:
        first, second = self.client(), self.client()
        self.execute_ok(first, "BEGIN;")
        self.execute_ok(first, "UPDATE student SET name = 'pending' WHERE id = 1;")
        session = self.qp._sessions.get_session(first._session_id)
        tx_id = session.tx_id
        self.execute_ok(second, "BEGIN;")
        self.execute_ok(second, "UPDATE blocked SET name = 'held' WHERE id = 1;")
        other_tx = self.qp._sessions.get_session(second._session_id).tx_id
        waiting = Event()
        with self.observe_wait(waiting), patch.object(self.ccm, "abort", wraps=self.ccm.abort) as abort:
            thread, _ = self.start_query(first, "UPDATE blocked SET name = 'late' WHERE id = 1;")
            self.assertTrue(waiting.wait(2), "query tidak mencapai WAIT")
            first._connection.shutdown(socket.SHUT_RDWR)
            self.wait_until(lambda: self.qp._sessions.get_session(session.session_id) is None,
                            "disconnect masih menahan transaksi WAIT")
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
            abort.assert_called_once_with(tx_id)

        result = self.execute_ok(second, "SELECT * FROM student;")
        self.assertEqual(result.rows, self.original)
        self.assertEqual(self.qp._sessions.get_session(second._session_id).tx_id, other_tx)
        self.assertEqual([row.values for row in self.sm.scan("blocked")], [[1, "held"]])

    def test_cancelled_insert_releases_reservation_and_never_writes_after_lock_is_released(self) -> None:
        holder, writer = self.client(), self.client()
        self.execute_ok(holder, "BEGIN;")
        self.execute_ok(holder, "SELECT * FROM student;")
        waiting = Event()
        with self.observe_wait(waiting), \
             patch.object(self.sm, "allocate_rid", wraps=self.sm.allocate_rid) as allocate, \
             patch.object(self.sm, "release_rid", wraps=self.sm.release_rid) as release, \
             patch.object(self.sm, "insert", wraps=self.sm.insert) as insert:
            self.start_query(writer, "INSERT INTO student VALUES (3, 'new');")
            self.assertTrue(waiting.wait(2))
            session = self.session_for_tx(self.waiting_tx_id)
            writer._connection.shutdown(socket.SHUT_RDWR)
            self.wait_until(lambda: self.qp._sessions.get_session(session.session_id) is None,
                            "INSERT yang dibatalkan belum selesai")
            allocate.assert_called_once_with("student")
            release.assert_called_once()
            insert.assert_not_called()
        self.execute_ok(holder, "COMMIT;")
        self.assertEqual(self.execute_ok(holder, "SELECT * FROM student;").rows, self.original)

    def test_live_waiting_client_continues_after_holder_commits(self) -> None:
        holder, writer = self.client(), self.client()
        self.execute_ok(holder, "BEGIN;")
        self.execute_ok(holder, "SELECT * FROM student;")
        waiting = Event()
        with self.observe_wait(waiting):
            thread, responses = self.start_query(writer, "INSERT INTO student VALUES (3, 'new');")
            self.assertTrue(waiting.wait(2))
            self.execute_ok(holder, "COMMIT;")
            thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(responses[0].ok, responses[0].message)
        self.assertEqual(self.execute_ok(holder, "SELECT * FROM student;").rows,
                         self.original + [[3, "new"]])

    def test_eof_cancels_wait_even_with_another_request_queued(self) -> None:
        holder = self.client()
        self.execute_ok(holder, "BEGIN;")
        self.execute_ok(holder, "SELECT * FROM student;")
        conn = socket.create_connection(("127.0.0.1", self.server.port), timeout=3)
        self.sockets.append(conn)
        waiting = Event()
        with self.observe_wait(waiting), \
             patch.object(self.ccm, "begin_transaction", wraps=self.ccm.begin_transaction) as begin:
            conn.sendall(b'{"sql":"INSERT INTO student VALUES (3, \'new\');"}\n')
            self.assertTrue(waiting.wait(2))
            session = self.session_for_tx(self.waiting_tx_id)
            conn.sendall(b'{"sql":"INSERT INTO student VALUES (4, \'queued\');"}\n')
            conn.shutdown(socket.SHUT_RDWR)
            self.wait_until(lambda: self.qp._sessions.get_session(session.session_id) is None,
                            "EOF dengan request antre masih menahan WAIT")
            begin.assert_called_once()
        self.assertEqual([row.values for row in self.sm.scan("student")], self.original)

    def test_pipelined_live_requests_preserve_response_order_and_protocol_errors(self) -> None:
        conn = socket.create_connection(("127.0.0.1", self.server.port), timeout=3)
        self.sockets.append(conn)
        conn.sendall(b'not-json\n{"sql":"SELECT id FROM student WHERE id = 1;"}\n'
                     b'{"sql":"SELECT id FROM student WHERE id = 2;"}\n')
        with conn.makefile("r", encoding="utf-8") as reader:
            responses = [json.loads(reader.readline()) for _ in range(3)]
        self.assertFalse(responses[0]["ok"])
        self.assertIn("PROTOCOL", responses[0]["message"])
        self.assertEqual(responses[1]["rows"], [[1]])
        self.assertEqual(responses[2]["rows"], [[2]])

    def test_shutdown_cancels_wait_and_undoes_active_clients(self) -> None:
        holder, writer = self.client(), self.client()
        self.execute_ok(holder, "BEGIN;")
        self.execute_ok(holder, "UPDATE student SET name = 'pending' WHERE id = 1;")
        waiting = Event()
        with self.observe_wait(waiting):
            self.start_query(writer, "DELETE FROM student;")
            self.assertTrue(waiting.wait(2))
            self.server.shutdown()
            self.wait_until(lambda: not self.qp._sessions.active_sessions(), "shutdown belum membersihkan sesi")
        self.assertEqual([row.values for row in self.sm.scan("student")], self.original)

    def test_failed_undo_after_disconnect_keeps_session_and_locks_for_cleanup_retry(self) -> None:
        first, second = self.client(), self.client()
        self.execute_ok(first, "BEGIN;")
        self.execute_ok(first, "UPDATE student SET name = 'pending' WHERE id = 1;")
        session = self.qp._sessions.get_session(first._session_id)
        tx_id = session.tx_id
        self.execute_ok(second, "BEGIN;")
        self.execute_ok(second, "UPDATE blocked SET name = 'held' WHERE id = 1;")
        rollback = self.frm.rollback
        def fail_undo(tx):
            return TxResult(tx, TxStatus.FAILED, False, "undo gagal") if tx == tx_id else rollback(tx)
        waiting = Event()
        with self.observe_wait(waiting), patch.object(self.frm, "rollback", side_effect=fail_undo), \
             self.assertLogs("coachdb.qp", level="ERROR"):
            self.start_query(first, "UPDATE blocked SET name = 'late' WHERE id = 1;")
            self.assertTrue(waiting.wait(2))
            first._connection.shutdown(socket.SHUT_RDWR)
            self.wait_until(lambda: not self.server._client_threads[0].is_alive(), "handler belum berhenti")
        self.assertIs(self.qp._sessions.get_session(session.session_id), session)
        self.assertEqual(session.tx_id, tx_id)
        self.assertEqual([row.values for row in self.sm.scan("student")], [[1, "pending"], [2, "two"]])
        self.qp.close_session(session.session_id)
        self.assertIsNone(self.qp._sessions.get_session(session.session_id))
        self.assertEqual([row.values for row in self.sm.scan("student")], self.original)


if __name__ == "__main__":
    unittest.main()
