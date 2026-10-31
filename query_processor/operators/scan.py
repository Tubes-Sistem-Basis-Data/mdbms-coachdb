from __future__ import annotations

from typing import Iterator

from common.storage import Row
from query_processor.context import ExecContext
from query_processor.operators.base import RowOperator
from query_processor.tuple import ExecRow, OutputSchema


class TableScanOp(RowOperator):
    """Membaca semua baris pada satu tabel"""

    def __init__(self, table: str, schema: OutputSchema) -> None:
        self._table = table
        self.schema = schema
        self._ctx: ExecContext | None = None
        self._cursor: Iterator[Row] | None = None

    def open(self, ctx: ExecContext) -> None:
        pass

    def next(self) -> ExecRow | None:
        return None

    def close(self) -> None:
        pass