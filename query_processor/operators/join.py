"""Join row operator skeleton owned by QP4."""

from __future__ import annotations

from common.expression import Expression
from common.plan import JoinKind
from query_processor.context import ExecContext
from query_processor.evaluator import ExpressionEvaluator
from query_processor.operators.base import RowOperator
from query_processor.tuple import ExecRow, OutputSchema


class BlockNestedLoopJoinOp(RowOperator):
    """Combine two child streams using the Block Nested Loop Join interface.

    The translator resolves NATURAL JOIN's common columns to index pairs and
    supplies the output schema. The left child is the outer input; the right
    child is the inner input, matching the optimizer's join cost model.
    """

    def __init__(
        self,
        left: RowOperator,
        right: RowOperator,
        schema: OutputSchema,
        join_kind: JoinKind,
        condition: Expression | None = None,
        evaluator: ExpressionEvaluator | None = None,
        shared_columns: tuple[tuple[int, int], ...] = (),
        block_size: int | None = None,
    ) -> None:
        """Store child operators and the resolved join configuration."""
        self._left = left
        self._right = right
        self._schema = schema
        self._join_kind = join_kind
        self._condition = condition
        self._evaluator = evaluator
        self._shared_columns = shared_columns
        self._block_size = block_size

    @property
    def schema(self) -> OutputSchema:
        """Return the output schema, with shared NATURAL JOIN columns once."""
        return self._schema

    def open(self, ctx: ExecContext) -> None:
        """Open child streams and prepare this operator for iteration."""
        return None

    def next(self) -> ExecRow | None:
        """Return the next matching joined row, or None when exhausted."""
        return None

    def close(self) -> None:
        """Close both child streams, including when iteration failed."""
        return None
