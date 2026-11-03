from __future__ import annotations

from typing import TYPE_CHECKING

from common.expression import Expression
from common.result import ExecutionResult
from query_processor.operators.base import Executable, RowOperator

if TYPE_CHECKING:
    from query_processor.context import ExecContext


class UpdateExecution(Executable):
    """Menyimpan tabel, kolom, ekspresi SET, dan sumber target UPDATE."""

    def __init__(self, table: str, column: str, value_expr: Expression, source: RowOperator) -> None:
        self._table = table
        self._column = column
        self._value_expr = value_expr
        self._source = source

    def execute(self, ctx: ExecContext) -> ExecutionResult:
        return ExecutionResult(ok=False)
