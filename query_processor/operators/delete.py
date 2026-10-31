from __future__ import annotations

from common.result import ExecutionResult
from query_processor.context import ExecContext
from query_processor.operators.base import Executable, RowOperator


class DeleteExecution(Executable):
    """Menjalankan DELETE"""

    def __init__(self, table: str, source: RowOperator) -> None:
        self._table = table
        self._source = source

    def execute(self, ctx: ExecContext) -> ExecutionResult:
        return ExecutionResult(ok=False, message="DELETE belum diimplementasi")