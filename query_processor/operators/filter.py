from __future__ import annotations

from common.expression import Expression
from query_processor.context import ExecContext
from query_processor.evaluator import ExpressionEvaluator
from query_processor.operators.base import RowOperator
from query_processor.tuple import ExecRow


class FilterOp(RowOperator):
    """Meloloskan baris yang memenuhi filter"""

    def __init__(self, child: RowOperator, condition: Expression) -> None:
        self._child = child
        self._condition = condition
        self._evaluator = ExpressionEvaluator()
        self.schema = child.schema

    def open(self, ctx: ExecContext) -> None:
        pass

    def next(self) -> ExecRow | None:
        return None

    def close(self) -> None:
        pass