from __future__ import annotations

from common.result import ExecutionResult

type QueryResult = ExecutionResult

__all__ = ["ExecutionResult", "QueryResult", "ResultFormatter"]


class ResultFormatter:
    def format(self, result: ExecutionResult) -> str:
        raise NotImplementedError("QP-01: ResultFormatter.format belum diimplementasikan")

    def format_error(self, error: Exception | str) -> str:
        raise NotImplementedError("QP-01: ResultFormatter.format_error belum diimplementasikan")
