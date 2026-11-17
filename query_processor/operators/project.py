from __future__ import annotations

from common.expression import ColumnRef
from common.result import ExecutionResult
from query_processor.context import ExecContext
from query_processor.errors import PlanError
from query_processor.operators.base import Executable, RowOperator
from query_processor.tuple import ColumnBinding, ExecRow, OutputSchema


class ProjectOp(RowOperator):
    """Memilih kolom keluaran"""

    def __init__(
        self,
        child: RowOperator,
        columns: list[ColumnRef],
        aliases: list[str | None] | None = None,
    ) -> None:
        self._child = child
        self._columns = columns
        self._positions = [child.schema.index_of(c) for c in columns]
        bindings = [child.schema.columns[i] for i in self._positions]
        self.schema = OutputSchema(bindings, _labels(bindings, aliases))

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


def _labels(
    bindings: list[ColumnBinding], aliases: list[str | None] | None) -> tuple[str, ...] | None:
    if aliases is None or (isinstance(aliases, list) and not aliases):
        return None
    if not isinstance(aliases, list) or len(aliases) != len(bindings):
        raise PlanError("Alias Project harus sejajar dengan kolom")
    for alias in aliases:
        if alias is not None and (not isinstance(alias, str) or not alias.strip()):
            raise PlanError("Alias kolom harus berupa teks yang tidak kosong")
    if all(alias is None for alias in aliases):
        return None
    return tuple(
        alias if alias is not None else binding.name
        for alias, binding in zip(aliases, bindings)
    )


class SelectExecution(Executable):
    """Menjalankan SELECT"""

    def __init__(self, root: RowOperator) -> None:
        self._root = root

    def execute(self, ctx: ExecContext) -> ExecutionResult:
        rows: list[list] = []
        try:
            self._root.open(ctx)
            while (row := self._root.next()) is not None:
                rows.append(list(row.values))
        finally:
            self._root.close()
        columns = self._root.schema.output_names()
        return ExecutionResult(ok=True, columns=columns, rows=rows)