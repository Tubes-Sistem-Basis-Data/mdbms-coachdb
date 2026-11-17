from __future__ import annotations

from query_processor.context import ExecContext
from query_processor.errors import PlanError
from query_processor.operators.base import RowOperator
from query_processor.tuple import ExecRow


class LimitOp(RowOperator):
    """Membatasi jumlah baris output"""

    def __init__(self, child: RowOperator, count: int) -> None:
        if type(count) is not int or count < 0:
            raise PlanError("Jumlah LIMIT harus berupa bilangan bulat nonnegatif")
        self._child = child
        self._count = count
        self._emitted = 0
        self._child_open = False
        self.schema = child.schema

    def open(self, ctx: ExecContext) -> None:
        self.close()
        self._emitted = 0
        if self._count == 0:
            return
        self._child_open = True
        try:
            self._child.open(ctx)
        except Exception:
            self._close_child_quietly()
            raise

    def next(self) -> ExecRow | None:
        if not self._child_open:
            return None
        try:
            row = self._child.next()
        except Exception:
            self._close_child_quietly()
            raise
        if row is None:
            return None
        self._emitted += 1
        if self._emitted >= self._count:
            self.close()
        return row

    def close(self) -> None:
        if self._child_open:
            self._child_open = False
            self._child.close()

    def _close_child_quietly(self) -> None:
        try:
            self.close()
        except Exception:
            pass