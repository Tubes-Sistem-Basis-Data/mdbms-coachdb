from __future__ import annotations

import json
import logging
import socket
import threading

from common import config
from common.interfaces import QueryProcessor, Session
from common.result import ExecutionResult

logger = logging.getLogger("coachdb.qp")


class QueryServer:
    def __init__(
        self,
        qp: QueryProcessor,
        host: str = config.HOST,
        port: int = config.PORT,
    ) -> None:
        self._qp = qp
        self._host = host
        self._port = port
        self._sock: socket.socket | None = None
        self._running: bool = False
        self._lock = threading.Lock()
        self._client_threads: list[threading.Thread] = []

    @property
    def port(self) -> int:
        if self._sock is not None:
            try:
                return int(self._sock.getsockname()[1])
            except OSError:
                pass
        return self._port

    def serve(self) -> None:
        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind((self._host, self._port))
        server_sock.listen()
        server_sock.settimeout(0.5)

        with self._lock:
            self._sock = server_sock
            self._running = True
            self._port = int(server_sock.getsockname()[1])

        try:
            while self._running:
                try:
                    conn, addr = server_sock.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break

                thread = threading.Thread(
                    target=self.handle_client,
                    args=(conn, addr),
                    daemon=True,
                )
                self._client_threads.append(thread)
                thread.start()
        finally:
            self.shutdown()

    def handle_client(
        self,
        conn: socket.socket,
        addr: tuple[str, int] | None = None,
    ) -> None:
        session: Session = self._qp.open_session()
        try:
            reader = conn.makefile("r", encoding="utf-8")
            for raw_line in reader:
                line = raw_line.strip()
                if not line:
                    continue

                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    err_res = ExecutionResult(
                        ok=False,
                        message="ERROR [PROTOCOL]: Permintaan bukan JSON yang valid",
                    )
                    self._send_response(conn, err_res, session.session_id)
                    continue

                if not isinstance(payload, dict):
                    err_res = ExecutionResult(
                        ok=False,
                        message="ERROR [PROTOCOL]: Payload JSON harus berupa objek",
                    )
                    self._send_response(conn, err_res, session.session_id)
                    continue

                action = payload.get("action")
                if action == "close":
                    break

                sql = payload.get("sql")
                if not isinstance(sql, str):
                    err_res = ExecutionResult(
                        ok=False,
                        message="ERROR [PROTOCOL]: Field 'sql' wajib berupa string",
                    )
                    self._send_response(conn, err_res, session.session_id)
                    continue

                result = self._qp.execute_query(sql, session)
                self._send_response(conn, result, session.session_id)
        except (ConnectionError, OSError):
            logger.debug("Koneksi client %s terputus", addr)
        except Exception:
            logger.exception("Kesalahan tidak terduga pada handler client %s", addr)
        finally:
            try:
                self._qp.close_session(session.session_id)
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    @staticmethod
    def _send_response(
        conn: socket.socket,
        result: ExecutionResult,
        session_id: int,
    ) -> None:
        data = result.to_dict()
        data["session_id"] = session_id
        wire = json.dumps(data, ensure_ascii=False) + "\n"
        conn.sendall(wire.encode("utf-8"))

    def shutdown(self) -> None:
        with self._lock:
            self._running = False
            sock = self._sock
            self._sock = None

        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
