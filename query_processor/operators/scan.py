from __future__ import annotations

from time import sleep
from typing import Iterator

from common.catalog import DataType, IndexInfo
from common.config import WAIT_RETRY_SECONDS
from common.errors import TransactionAbortedError
from common.expression import ColumnRef, Comparison, CompOp, Literal
from common.storage import RID, Row
from common.transaction import AccessStatus, Action
from query_processor.context import ExecContext
from query_processor.evaluator import ExpressionEvaluator
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
        self._ctx: ExecContext | None = None
        self._position = 0
        self._conditions: list[Comparison] = []

    def open(self, ctx: ExecContext) -> None:
        self.close()

        # Siapkan kondisi untuk memeriksa hasil pencarian index
        column = ColumnRef(None, self._index.column)
        bounds = []

        if self._op == CompOp.EQ:
            bounds.append((CompOp.EQ, self._key))
        else:
            if self._low is not None:
                lower_op = CompOp.GE
                if self._op == CompOp.GT:
                    lower_op = CompOp.GT
                bounds.append((lower_op, self._low))

            if self._high is not None:
                upper_op = CompOp.LE
                if self._op == CompOp.LT:
                    upper_op = CompOp.LT
                bounds.append((upper_op, self._high))

        conditions = []
        for op, value in bounds:
            if type(value) is int:
                data_type = DataType.INTEGER
            elif type(value) is float:
                data_type = DataType.FLOAT
            else:
                data_type = DataType.VARCHAR

            condition = Comparison(op, column, Literal(value, data_type))
            conditions.append(condition)

        # Dapatkan izin sebelum membaca index
        response = ctx.authorize_table(self._table)
        while response.status == AccessStatus.WAIT:
            sleep(WAIT_RETRY_SECONDS)
            response = ctx.authorize_table(self._table)
            
        if response.status != AccessStatus.ALLOWED:
            raise TransactionAbortedError(response.message, ctx.tx_id)

        # Ambil RID lewat index
        if self._op == CompOp.EQ:
            if self._key is None:
                rids = []
            else:
                rids = ctx.sm.index_lookup(self._table, self._index.column, self._key)
        else:
            rids = ctx.sm.index_range(self._table, self._index.column, self._low, self._high)
        self._rids = list(rids)
        self._conditions = conditions
        self._ctx = ctx

    def next(self) -> ExecRow | None:
        if self._ctx is None:
            return None

        ctx = self._ctx
        evaluator = ExpressionEvaluator()

        try:
            while self._position < len(self._rids):
                rid = self._rids[self._position]

                # Dapatkan izin sebelum membaca baris
                response = ctx.authorize_rid(self._table, rid, Action.READ)
                while response.status == AccessStatus.WAIT:
                    sleep(WAIT_RETRY_SECONDS)
                    response = ctx.authorize_rid(self._table, rid, Action.READ)
                    
                if response.status != AccessStatus.ALLOWED:
                    raise TransactionAbortedError(response.message, ctx.tx_id)

                row = ctx.sm.fetch(self._table, rid)
                self._position += 1
                if row is None:
                    continue

                result = ExecRow(tuple(row.values), self.schema, row.rid)

                # Periksa batas ketat dan nilai terbaru
                matches = True
                for condition in self._conditions:
                    if not evaluator.eval_bool(condition, result):
                        matches = False
                        break

                if matches:
                    return result
                
        except Exception:
            self.close()
            raise
        return None

    def close(self) -> None:
        self._ctx = None
        self._rids = []
        self._position = 0
        self._conditions = []
