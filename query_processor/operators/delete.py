from __future__ import annotations

from common.errors import TransactionAbortedError
from common.result import ExecutionResult
from common.transaction import NO_RECORD, AccessStatus, Action, ChangeOp, DataChange
from query_processor.context import ExecContext
from query_processor.operators.base import Executable, RowOperator


class DeleteExecution(Executable):
    """Menjalankan DELETE"""

    def __init__(self, table: str, source: RowOperator) -> None:
        self._table = table
        self._source = source

    def execute(self, ctx: ExecContext) -> ExecutionResult:
        rows_to_delete = []
        self._source.open(ctx)
        try:
            while (row := self._source.next()) is not None:
                rows_to_delete.append(row)
        finally:
            self._source.close()

        for row in rows_to_delete:
            change = DataChange(
                self._table, row.rid, ChangeOp.DELETE, list(row.values), NO_RECORD
            )
            response = ctx.authorize_rid(self._table, row.rid, Action.WRITE, change)
            if response.status != AccessStatus.ALLOWED:
                raise TransactionAbortedError(response.message, ctx.tx_id)
            ctx.sm.delete(self._table, row.rid, response.lsn)
        return ExecutionResult(ok=True, affected_rows=len(rows_to_delete))