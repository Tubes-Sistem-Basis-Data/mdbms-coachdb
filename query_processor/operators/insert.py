from __future__ import annotations

from typing import TYPE_CHECKING

from common.result import ExecutionResult
from query_processor.operators.base import Executable

if TYPE_CHECKING:
    from query_processor.context import ExecContext
    from query_processor.tuple import Value


class InsertExecution(Executable):
    """Menyimpan tabel dan nilai INSERT"""

    def __init__(self, table: str, values: tuple[Value, ...]) -> None:
        self._table = table
        self._values = values

    def execute(self, ctx: ExecContext) -> ExecutionResult:
        raise NotImplementedError("QP-10: InsertExecution.execute belum diimplementasikan")
