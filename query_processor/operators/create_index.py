from __future__ import annotations

from typing import TYPE_CHECKING

from common.catalog import IndexInfo
from common.result import ExecutionResult
from query_processor.operators.base import Executable

if TYPE_CHECKING:
    from query_processor.context import ExecContext


class CreateIndexExecution(Executable):
    """Menerima metadata index"""

    def __init__(self, index: IndexInfo) -> None:
        self._index = index

    def execute(self, ctx: ExecContext) -> ExecutionResult:
        return ExecutionResult(ok=False)
