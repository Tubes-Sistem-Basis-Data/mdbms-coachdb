from __future__ import annotations

from common.expression import ColumnRef
from common.result import ExecutionResult
from query_processor.context import ExecContext
from query_processor.operators.base import Executable, RowOperator
from query_processor.tuple import ExecRow, OutputSchema


class ProjectOp(RowOperator):
    """Memilih kolom keluaran"""

    def __init__(self, child: RowOperator, columns: list[ColumnRef]) -> None:
        self._child = child
        self._columns = columns
        self._positions: list[int] = []
        self.schema = OutputSchema([])

    def open(self, ctx: ExecContext) -> None:
        return None

    def next(self) -> ExecRow | None:
        return None

    def close(self) -> None:
        return None


class SelectExecution(Executable):
    """Menjalankan SELECT"""

    def __init__(self, root: RowOperator) -> None:
        self._root = root

    def execute(self, ctx: ExecContext) -> ExecutionResult:
        return ExecutionResult(ok=False, message="SELECT belum diimplementasi")