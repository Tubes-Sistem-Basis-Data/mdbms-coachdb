from __future__ import annotations

from time import sleep
from typing import TYPE_CHECKING

from common.catalog import IndexInfo
from common.config import WAIT_RETRY_SECONDS
from common.errors import TransactionAbortedError
from common.result import ExecutionResult
from common.transaction import AccessStatus
from query_processor.operators.base import Executable

if TYPE_CHECKING:
    from query_processor.context import ExecContext


class CreateIndexExecution(Executable):
    """Membuat index setelah mendapatkan izin membaca tabel"""

    def __init__(self, index: IndexInfo) -> None:
        self._index = index

    def execute(self, ctx: ExecContext) -> ExecutionResult:
        # Dapatkan izin membaca tabel untuk membangun index
        response = ctx.authorize_table(self._index.table)
        
        while response.status == AccessStatus.WAIT:
            sleep(WAIT_RETRY_SECONDS)
            response = ctx.authorize_table(self._index.table)
            
        if response.status != AccessStatus.ALLOWED:
            raise TransactionAbortedError(response.message, ctx.tx_id)

        ctx.sm.create_index(self._index)
        message = (
            f"Index '{self._index.name}' dibuat pada "
            f"{self._index.table}({self._index.column})"
        )
        
        return ExecutionResult(ok=True, message=message)
