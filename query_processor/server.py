from __future__ import annotations

import socket

from common import config
from common.interfaces import QueryProcessor


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

    def serve(self) -> None:
        return None

    def handle_client(
        self,
        conn: socket.socket,
        addr: tuple[str, int] | None = None,
    ) -> None:
        return None

    def shutdown(self) -> None:
        return None
