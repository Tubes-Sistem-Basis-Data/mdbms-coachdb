from __future__ import annotations

from typing import Iterator

from common.catalog import IndexInfo
from common.expression import CompOp
from common.errors import TransactionAbortedError
from common.storage import RID, Row
from common.transaction import AccessStatus
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
        response = ctx.authorize_table(self._table)
        if response.status != AccessStatus.ALLOWED:
            raise TransactionAbortedError(response.message, ctx.tx_id)
        self._ctx = ctx
        self._cursor = iter(ctx.sm.scan(self._table))

    def next(self) -> ExecRow | None:
        if self._cursor is None:
            return None
        row = next(self._cursor, None)
        if row is None:
            return None
        return ExecRow(tuple(row.values), self.schema, row.rid)

    def close(self) -> None:
        self._cursor = None
        self._ctx = None


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
        return None

    def next(self) -> ExecRow | None:
        return None

    def close(self) -> None:
        return None
