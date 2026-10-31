from __future__ import annotations

from typing import Iterator

from common.catalog import IndexInfo
from common.expression import CompOp
from common.storage import RID, Row
from query_processor.context import ExecContext
from query_processor.operators.base import RowOperator
from query_processor.tuple import ExecRow, OutputSchema, Value


class TableScanOp(RowOperator):
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


class IndexScanOp(RowOperator):
    def __init__(
        self,
        table: str,
        index: IndexInfo,
        op: CompOp,
        schema: OutputSchema,
        key: Value = None,
        low: Value = None,
        high: Value = None,
    ) -> None:
        self._table = table
        self._index = index
        self._op = op
        self.schema = schema
        self._key = key
        self._low = low
        self._high = high
        self._rids: list[RID] = []

    def open(self, ctx: ExecContext) -> None:
        raise NotImplementedError("QP-13: IndexScanOp.open belum diimplementasikan")

    def next(self) -> ExecRow | None:
        raise NotImplementedError("QP-13: IndexScanOp.next belum diimplementasikan")

    def close(self) -> None:
        raise NotImplementedError("QP-13: IndexScanOp.close belum diimplementasikan")