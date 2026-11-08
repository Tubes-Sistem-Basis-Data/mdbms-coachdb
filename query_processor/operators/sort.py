"""Operator sort (QP4)."""

from __future__ import annotations

from common.expression import ColumnRef
from query_processor.context import ExecContext
from query_processor.errors import ExecutionError, PlanError
from query_processor.operators.base import RowOperator
from query_processor.tuple import ExecRow, OutputSchema


class SortOp(RowOperator):
    """Urutkan baris child berdasarkan satu kolom output."""

    def __init__(
        self,
        child: RowOperator,
        column: ColumnRef,
        ascending: bool = True,
    ) -> None:
        """Simpan child, kolom pengurut, dan arah urutan."""
        if type(ascending) is not bool:
            raise PlanError("Arah Sort harus berupa boolean")
        self._child = child
        self._column = column
        self._ascending = ascending
        self._position = child.schema.index_of(column)
        self._rows: list[ExecRow] = []
        self._position_in_rows = 0
        self._child_open = False
        self._opened = False

    @property
    def schema(self) -> OutputSchema:
        """Skema sama dengan child."""
        return self._child.schema

    def open(self, ctx: ExecContext) -> None:
        """Materialize dan urutkan child sebelum mengeluarkan baris pertama."""
        if self._child_open or self._opened:
            self.close()

        self._rows = []
        self._position_in_rows = 0
        self._opened = False

        try:
            self._child_open = True
            self._child.open(ctx)
            rows: list[ExecRow] = []
            while (row := self._child.next()) is not None:
                self._validate_row(row)
                rows.append(row)

            try:
                self._rows = sorted(
                    rows,
                    key=self._sort_key,
                    reverse=not self._ascending,
                )
            except TypeError as exc:
                raise ExecutionError(
                    "Nilai ORDER BY tidak kompatibel dengan tipe kolom"
                ) from exc

            self._child.close()
            self._child_open = False
            self._opened = True
        except BaseException:
            try:
                self.close()
            except BaseException:
                pass
            raise

    def next(self) -> ExecRow | None:
        """Baris berikutnya sesuai urutan, None jika sudah habis."""
        if not self._opened or self._position_in_rows >= len(self._rows):
            return None
        row = self._rows[self._position_in_rows]
        self._position_in_rows += 1
        return row

    def close(self) -> None:
        """Release the materialized rows and close the child if still open."""
        child_open = self._child_open
        self._child_open = False
        self._opened = False
        self._rows = []
        self._position_in_rows = 0
        if child_open:
            self._child.close()

    def _sort_key(self, row: ExecRow) -> tuple[bool, object]:
        value = row.values[self._position]
        return value is None, 0 if value is None else value

    def _validate_row(self, row: ExecRow) -> None:
        if (
            not isinstance(row, ExecRow)
            or not isinstance(row.values, tuple)
            or not isinstance(row.schema, OutputSchema)
            or len(row.values) != len(self._child.schema.columns)
            or len(row.schema.columns) != len(self._child.schema.columns)
        ):
            raise ExecutionError("Jumlah nilai baris SORT tidak sesuai skema child")
