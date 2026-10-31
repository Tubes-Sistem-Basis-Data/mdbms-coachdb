from __future__ import annotations

import socket

from common import config
from common.result import ExecutionResult
from query_processor.result import ResultFormatter


class StatementSplitter:
    def __init__(self) -> None:
        self._buffer: str = ""
        self._in_string: bool = False

    def feed(self, text: str) -> list[str]:
        raise NotImplementedError("QP-01: StatementSplitter.feed belum diimplementasikan")

    def has_pending(self) -> bool:
        raise NotImplementedError("QP-01: StatementSplitter.has_pending belum diimplementasikan")

    def reset(self) -> None:
        raise NotImplementedError("QP-01: StatementSplitter.reset belum diimplementasikan")


class ClientCLI:
    def __init__(
        self,
        host: str = config.HOST,
        port: int = config.PORT,
    ) -> None:
        self._host = host
        self._port = port
        self._connection: socket.socket | None = None
        self._session_id: int | None = None
        self._splitter = StatementSplitter()
        self._formatter = ResultFormatter()

    def connect(self) -> None:
        raise NotImplementedError("QP-01: ClientCLI.connect belum diimplementasikan")

    def close(self) -> None:
        raise NotImplementedError("QP-01: ClientCLI.close belum diimplementasikan")

    def read_statement(self) -> str | None:
        raise NotImplementedError("QP-01: ClientCLI.read_statement belum diimplementasikan")

    def send(self, sql: str) -> ExecutionResult:
        raise NotImplementedError("QP-01: ClientCLI.send belum diimplementasikan")

    def run(self) -> None:
        raise NotImplementedError("QP-01: ClientCLI.run belum diimplementasikan")


def main() -> None:
    raise NotImplementedError("QP-01: entrypoint client.py belum diimplementasikan")


if __name__ == "__main__":
    main()
