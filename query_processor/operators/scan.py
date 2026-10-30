from __future__ import annotations

from typing import TYPE_CHECKING

from common.catalog import IndexInfo
from common.expression import CompOp
from common.storage import RID
from query_processor.operators.base import RowOperator

if TYPE_CHECKING:
    from query_processor.context import ExecContext
    from query_processor.tuple import ExecRow, OutputSchema, Value


class IndexScanOp(RowOperator):
    """Menyimpan parameter node IndexScan beserta skema hasil tabel."""

    def __init__(self, table: str, index: IndexInfo, op: CompOp, schema: OutputSchema, key: Value = None, low: Value = None, high: Value = None) -> None:
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
