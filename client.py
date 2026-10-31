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
        return []

    def has_pending(self) -> bool:
        return False

    def reset(self) -> None:
        return None


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
        return None

    def close(self) -> None:
        return None

    def read_statement(self) -> str | None:
        return None

    def send(self, sql: str) -> ExecutionResult:
        return ExecutionResult(ok=False)

    def run(self) -> None:
        return None


def main() -> None:
    return None


if __name__ == "__main__":
    main()
