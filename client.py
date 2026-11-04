from __future__ import annotations

import argparse
import json
import socket
import sys
from typing import TextIO

from common import config
from common.interfaces import QueryProcessor, Session
from common.result import ExecutionResult
from query_processor.result import ResultFormatter

_EXIT_COMMANDS = {"exit", "quit", "\\q"}


class StatementSplitter:
    def __init__(self) -> None:
        self._buffer: str = ""
        self._in_string: bool = False
        self._quote_char: str = "'"

    def feed(self, text: str) -> list[str]:
        if not isinstance(text, str):
            return []

        if self._buffer and not self._buffer.endswith("\n"):
            self._buffer += "\n"

        statements: list[str] = []
        current_chars: list[str] = list(self._buffer)
        in_string = self._in_string
        quote_char = self._quote_char

        i = 0
        n = len(text)
        while i < n:
            ch = text[i]
            if in_string:
                current_chars.append(ch)
                if ch == "\\":
                    if i + 1 < n:
                        current_chars.append(text[i + 1])
                        i += 2
                        continue
                elif ch == quote_char:
                    if i + 1 < n and text[i + 1] == quote_char:
                        current_chars.append(text[i + 1])
                        i += 2
                        continue
                    in_string = False
                i += 1
                continue

            if ch in ("'", '"'):
                in_string = True
                quote_char = ch
                current_chars.append(ch)
                i += 1
                continue

            if ch == "-" and i + 1 < n and text[i + 1] == "-":
                newline_pos = text.find("\n", i + 2)
                if newline_pos == -1:
                    break
                i = newline_pos
                continue

            if ch == ";":
                stmt_body = "".join(current_chars).strip()
                current_chars.clear()
                if stmt_body:
                    statements.append(f"{stmt_body};")
                i += 1
                continue

            current_chars.append(ch)
            i += 1

        self._buffer = "".join(current_chars)
        self._in_string = in_string
        self._quote_char = quote_char
        return statements

    def has_pending(self) -> bool:
        return self._in_string or bool(self._buffer.strip())

    def reset(self) -> None:
        self._buffer = ""
        self._in_string = False
        self._quote_char = "'"


class ClientCLI:
    PRIMARY_PROMPT = "coachdb> "
    CONTINUE_PROMPT = "    ...> "

    def __init__(
        self,
        host: str = config.HOST,
        port: int = config.PORT,
        qp: QueryProcessor | None = None,
        input_stream: TextIO | None = None,
        output_stream: TextIO | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._qp = qp
        self._connection: socket.socket | None = None
        self._reader: TextIO | None = None
        self._session: Session | None = None
        self._session_id: int | None = None
        self._splitter = StatementSplitter()
        self._formatter = ResultFormatter()
        self._input = input_stream
        self._output = output_stream
        self._pending_statements: list[str] = []

    def connect(self) -> None:
        if self._qp is not None:
            if self._session is None:
                self._session = self._qp.open_session()
                self._session_id = self._session.session_id
            return

        if self._connection is None:
            sock = socket.create_connection((self._host, self._port))
            self._connection = sock
            self._reader = sock.makefile("r", encoding="utf-8")

    def close(self) -> None:
        if self._qp is not None and self._session is not None:
            session_id = self._session.session_id
            self._session = None
            self._session_id = None
            self._qp.close_session(session_id)

        if self._reader is not None:
            try:
                self._reader.close()
            except OSError:
                pass
            self._reader = None

        if self._connection is not None:
            try:
                self._connection.close()
            except OSError:
                pass
            self._connection = None

    def read_statement(self) -> str | None:
        if self._pending_statements:
            return self._pending_statements.pop(0)

        in_stream = self._input if self._input is not None else sys.stdin
        out_stream = self._output if self._output is not None else sys.stdout

        while True:
            prompt = self.CONTINUE_PROMPT if self._splitter.has_pending() else self.PRIMARY_PROMPT
            try:
                if in_stream is sys.stdin and self._output is None:
                    line = input(prompt)
                else:
                    out_stream.write(prompt)
                    out_stream.flush()
                    raw = in_stream.readline()
                    if raw == "":
                        raise EOFError
                    line = raw.rstrip("\r\n")
            except EOFError:
                self._splitter.reset()
                return None
            except KeyboardInterrupt:
                out_stream.write("\n")
                out_stream.flush()
                if self._splitter.has_pending():
                    self._splitter.reset()
                    continue
                return None

            if not self._splitter.has_pending():
                stripped = line.strip()
                if not stripped:
                    continue
                cmd_normalized = stripped.rstrip(";").strip().lower()
                if cmd_normalized in _EXIT_COMMANDS:
                    return None

            statements = self._splitter.feed(line)
            if statements:
                first = statements[0]
                if len(statements) > 1:
                    self._pending_statements.extend(statements[1:])
                if first.rstrip(";").strip().lower() in _EXIT_COMMANDS:
                    self._pending_statements.clear()
                    return None
                return first

    def send(self, sql: str) -> ExecutionResult:
        if self._qp is not None:
            if self._session is None:
                self.connect()
            assert self._session is not None
            try:
                return self._qp.execute_query(sql, self._session)
            except Exception as exc:
                return ExecutionResult(ok=False, message=self._formatter.format_error(exc))

        if self._connection is None or self._reader is None:
            try:
                self.connect()
            except OSError as exc:
                return ExecutionResult(
                    ok=False,
                    message=f"ERROR [CONNECTION]: Gagal terhubung ke server {self._host}:{self._port} ({exc})",
                )

        assert self._connection is not None
        assert self._reader is not None

        request_payload = {
            "session_id": self._session_id,
            "sql": sql,
        }
        try:
            wire = json.dumps(request_payload, ensure_ascii=False) + "\n"
            self._connection.sendall(wire.encode("utf-8"))
            response_line = self._reader.readline()
            if not response_line:
                return ExecutionResult(
                    ok=False,
                    message="ERROR [CONNECTION]: Server menutup koneksi",
                )
            data = json.loads(response_line)
            if isinstance(data, dict) and "session_id" in data and data["session_id"] is not None:
                self._session_id = int(data["session_id"])
            return ExecutionResult.from_dict(data)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            return ExecutionResult(
                ok=False,
                message=f"ERROR [CONNECTION]: Komunikasi dengan server gagal ({exc})",
            )

    def run(self) -> None:
        out_stream = self._output if self._output is not None else sys.stdout
        try:
            self.connect()
        except OSError as exc:
            out_stream.write(
                self._formatter.format_error(
                    f"Tidak dapat terhubung ke server {self._host}:{self._port} ({exc})"
                )
                + "\n"
            )
            out_stream.flush()
            return

        try:
            while True:
                stmt = self.read_statement()
                if stmt is None:
                    break
                result = self.send(stmt)
                rendered = self._formatter.format(result)
                if rendered:
                    out_stream.write(rendered + "\n")
                    out_stream.flush()
        finally:
            self.close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CoachDB Interactive CLI Client")
    parser.add_argument("--host", default=config.HOST, help="alamat host server CoachDB")
    parser.add_argument("--port", type=int, default=config.PORT, help="port TCP server CoachDB")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    cli = ClientCLI(host=args.host, port=args.port)
    cli.run()


if __name__ == "__main__":
    main()
