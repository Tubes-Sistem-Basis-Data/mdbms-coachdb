from __future__ import annotations

from common.plan import ParsedQuery, PlanNode
from common.result import ExecutionResult
from query_processor.context import ExecContext
from query_processor.operators.base import Executable, RowOperator
from query_processor.tuple import ExecRow, OutputSchema


class _NoOpExecutable(Executable):
    """Executable kosong, dikembalikan PlanTranslator selama belum diimplementasikan."""

    def execute(self, ctx: ExecContext) -> ExecutionResult:
        return ExecutionResult(ok=False)


class _NoOpRowOperator(RowOperator):
    """RowOperator kosong, dikembalikan PlanTranslator selama belum diimplementasikan."""

    schema: OutputSchema = OutputSchema(columns=[])

    def open(self, ctx: ExecContext) -> None:
        return None

    def next(self) -> ExecRow | None:
        return None

    def close(self) -> None:
        return None


class PlanTranslator:
    def build(self, parsed: ParsedQuery, ctx: ExecContext) -> Executable:
        return _NoOpExecutable()

    def build_rows(self, node: PlanNode, ctx: ExecContext) -> RowOperator:
        return _NoOpRowOperator()

    def validate(self, node: PlanNode) -> None:
        return None
