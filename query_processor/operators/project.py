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
        self._positions = [child.schema.index_of(c) for c in columns]
        self.schema = OutputSchema([child.schema.columns[i] for i in self._positions])

    def open(self, ctx: ExecContext) -> None:
        self._child.open(ctx)

    def next(self) -> ExecRow | None:
        row = self._child.next()
        if row is None:
            return None
        values = tuple(row.values[i] for i in self._positions)
        return ExecRow(values, self.schema, row.rid)

    def close(self) -> None:
        self._child.close()


class SelectExecution(Executable):
    """Menjalankan SELECT"""

    def __init__(self, root: RowOperator) -> None:
        self._root = root

    def execute(self, ctx: ExecContext) -> ExecutionResult:
        rows: list[list] = []
        self._root.open(ctx)
        try:
            while (row := self._root.next()) is not None:
                rows.append(list(row.values))
        finally:
            self._root.close()
        columns = [binding.name for binding in self._root.schema.columns]
        return ExecutionResult(ok=True, columns=columns, rows=rows)