from __future__ import annotations

from common.expression import Expression
from query_processor.context import ExecContext
from query_processor.evaluator import ExpressionEvaluator
from query_processor.operators.base import RowOperator
from query_processor.tuple import ExecRow


class FilterOp(RowOperator):
    """Meloloskan baris yang memenuhi filter"""

    def __init__(
        self, child: RowOperator, condition: Expression, evaluator: ExpressionEvaluator | None = None,
    ) -> None:
        self._child = child
        self._condition = condition
        self._evaluator = evaluator if evaluator is not None else ExpressionEvaluator()
        self.schema = child.schema

    def open(self, ctx: ExecContext) -> None:
        self._child.open(ctx)

    def next(self) -> ExecRow | None:
        while (row := self._child.next()) is not None:
            if self._evaluator.eval_bool(self._condition, row):
                return row
        return None

    def close(self) -> None:
        self._child.close()
