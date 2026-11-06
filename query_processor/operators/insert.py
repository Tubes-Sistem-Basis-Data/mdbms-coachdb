from __future__ import annotations

from typing import TYPE_CHECKING

from common.errors import TransactionAbortedError
from common.result import ExecutionResult
from common.transaction import NO_RECORD, AccessStatus, ChangeOp, DataChange
from query_processor.errors import ExecutionError
from query_processor.operators.base import Executable

if TYPE_CHECKING:
    from query_processor.context import ExecContext
    from query_processor.tuple import Value


class InsertExecution(Executable):
    """Menambah satu baris setelah validasi skema dan izin WRITE."""

    def __init__(self, table: str, values: tuple[Value, ...]) -> None:
        self._table = table
        self._values = values

    def execute(self, ctx: ExecContext) -> ExecutionResult:
        # Periksa dan normalisasi nilai
        values = ctx.sm.validate(self._table, list(self._values))
        
        # Alokasi tempat untuk baris baru
        rid = ctx.sm.allocate_rid(self._table)
        inserted = False
        
        try:
            change = DataChange(
                table=self._table,
                rid=rid,
                op=ChangeOp.INSERT,
                before=NO_RECORD,
                after=list(values),
            )
            
            # Dapatkan izin untuk insert
            response = ctx.authorize_insert(change)
            if response.status == AccessStatus.ABORTED:
                raise TransactionAbortedError(response.message, ctx.tx_id)
            if response.status != AccessStatus.ALLOWED:
                raise ExecutionError("Izin INSERT belum selesai ditangani oleh ExecContext")
            if response.lsn is None:
                raise ExecutionError("Izin WRITE untuk INSERT tidak menyertakan LSN")

            ctx.sm.insert(self._table, rid, list(values), response.lsn)
            inserted = True
        finally:
            if not inserted:
                ctx.sm.release_rid(rid)

        return ExecutionResult(ok=True, affected_rows=1)
