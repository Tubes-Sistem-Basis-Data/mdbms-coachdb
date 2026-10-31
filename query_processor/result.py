from __future__ import annotations

from common.result import ExecutionResult

QueryResult = ExecutionResult

__all__ = ["ExecutionResult", "QueryResult", "ResultFormatter"]


class ResultFormatter:
    def format(self, result: ExecutionResult) -> str:
        return ""

    def format_error(self, error: Exception | str) -> str:
        return ""
