from __future__ import annotations

from typing import TYPE_CHECKING

from common.errors import TransactionAbortedError
from common.expression import ColumnRef, Expression
from common.result import ExecutionResult
from common.transaction import AccessStatus, Action, ChangeOp, DataChange
from query_processor.errors import ExecutionError
from query_processor.evaluator import ExpressionEvaluator
from query_processor.operators.base import Executable, RowOperator

if TYPE_CHECKING:
    from query_processor.context import ExecContext


class UpdateExecution(Executable):
    """Mengubah satu kolom pada baris hasil source"""

    def __init__(self, table: str, column: str, value_expr: Expression, source: RowOperator) -> None:
        self._table = table
        self._column = column
        self._value_expr = value_expr
        self._source = source

    def execute(self, ctx: ExecContext) -> ExecutionResult:
        rows_to_update = []
        
        try:
            self._source.open(ctx)
            while (row := self._source.next()) is not None:
                if row.rid is None or row.rid.table != self._table:
                    raise ExecutionError("Target UPDATE harus memiliki RID dari tabel tujuan")
                rows_to_update.append(row)
        finally:
            self._source.close()

        evaluator = ExpressionEvaluator()
        for row in rows_to_update:
            
            # Hitung nilai SET dari nilai lama
            position = row.schema.index_of(ColumnRef(self._table, self._column))
            values = list(row.values)
            values[position] = evaluator.eval(self._value_expr, row)
            
            values = ctx.sm.validate(self._table, values, rid=row.rid)
            change = DataChange(
                self._table, row.rid, ChangeOp.UPDATE, list(row.values), list(values)
            )
            
            response = ctx.authorize_rid(self._table, row.rid, Action.WRITE, change)
            if response.status != AccessStatus.ALLOWED:
                raise TransactionAbortedError(response.message, ctx.tx_id)
            
            ctx.sm.update(self._table, row.rid, values, response.lsn)
        return ExecutionResult(ok=True, affected_rows=len(rows_to_update))
