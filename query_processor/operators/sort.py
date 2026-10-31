"""Operator sort (QP4)."""

from __future__ import annotations

from common.expression import ColumnRef
from query_processor.context import ExecContext
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
        self._child = child
        self._column = column
        self._ascending = ascending

    @property
    def schema(self) -> OutputSchema:
        """Skema sama dengan child."""
        return self._child.schema

    def open(self, ctx: ExecContext) -> None:
        """Buka child dan siapkan hasil terurut."""
        return None

    def next(self) -> ExecRow | None:
        """Baris berikutnya sesuai urutan, None jika sudah habis."""
        return None

    def close(self) -> None:
        """Tutup child."""
        return None
