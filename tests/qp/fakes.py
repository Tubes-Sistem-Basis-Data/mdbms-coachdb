from __future__ import annotations

from common.catalog import DataType
from common.storage import RID, Row
from common.transaction import AccessResponse, AccessStatus
from query_processor.context import ExecContext
from query_processor.operators.base import RowOperator
from query_processor.tuple import ColumnBinding, ExecRow, OutputSchema

SCHEMA = OutputSchema(
    [
        ColumnBinding("t", "id", DataType.INTEGER),
        ColumnBinding("t", "name", DataType.VARCHAR),
    ]
)
ROWS = [Row(RID("t", 0, i), [i, f"n{i}"]) for i in range(4)]


class FakeSM:
    def __init__(self) -> None:
        self.rows = list(ROWS)
        self.deleted: list[tuple[RID, int | None]] = []

    def scan(self, table):
        return iter(list(self.rows))

    def delete(self, table, rid, lsn):
        self.deleted.append((rid, lsn))
        self.rows = [r for r in self.rows if r.rid != rid]


class FakeCtx(ExecContext):
    def __init__(self, sm, status=AccessStatus.ALLOWED):
        super().__init__(tx_id=1, sm=sm, ccm=None)
        self.status = status
        self.calls: list[tuple] = []

    def authorize_table(self, table):
        self.calls.append(("table", table))
        return AccessResponse(status=self.status, message="ditolak")

    def authorize_rid(self, table, rid, action, change=None):
        self.calls.append(("rid", rid, action, change))
        return AccessResponse(status=self.status, lsn=7, message="ditolak")


class FakeSource(RowOperator):
    def __init__(self, rows: list[Row] = ROWS) -> None:
        self._rows = rows
        self._pos: int | None = None
        self.schema = SCHEMA
        self.closed = False

    def open(self, ctx: ExecContext) -> None:
        self._pos = 0

    def next(self) -> ExecRow | None:
        if self._pos is None or self._pos >= len(self._rows):
            return None
        row = self._rows[self._pos]
        self._pos += 1
        return ExecRow(tuple(row.values), self.schema, row.rid)

    def close(self) -> None:
        self._pos = None
        self.closed = True


def drain(op):
    out = []
    while (row := op.next()) is not None:
        out.append(row)
    return out