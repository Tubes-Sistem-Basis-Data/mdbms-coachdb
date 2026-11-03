from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from common.result import ExecutionResult

if TYPE_CHECKING:
    from query_processor.context import ExecContext
    from query_processor.tuple import ExecRow, OutputSchema


class RowOperator(ABC):
    schema: OutputSchema

    @abstractmethod
    def open(self, ctx: ExecContext) -> None:
        return None

    @abstractmethod
    def next(self) -> ExecRow | None:
        return None

    @abstractmethod
    def close(self) -> None:
        return None


class Executable(ABC):
    @abstractmethod
    def execute(self, ctx: ExecContext) -> ExecutionResult:
        return ExecutionResult(ok=False)
