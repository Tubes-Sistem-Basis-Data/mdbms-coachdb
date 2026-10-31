"""Sort row operator skeleton owned by QP4."""

from __future__ import annotations

from common.expression import ColumnRef
from query_processor.context import ExecContext
from query_processor.operators.base import RowOperator
from query_processor.tuple import ExecRow, OutputSchema


class SortOp(RowOperator):
    """Order a child stream by one resolved output column."""

    def __init__(
        self,
        child: RowOperator,
        column: ColumnRef,
        ascending: bool = True,
    ) -> None:
        """Store the child stream, sort key, and direction."""
        self._child = child
        self._column = column
        self._ascending = ascending

    @property
    def schema(self) -> OutputSchema:
        """Return the child's schema unchanged."""
        return self._child.schema

    def open(self, ctx: ExecContext) -> None:
        """Open the child and prepare sorted output."""
        raise NotImplementedError

    def next(self) -> ExecRow | None:
        """Return the next row in sort order, or None when exhausted."""
        raise NotImplementedError

    def close(self) -> None:
        """Close the child stream."""
        raise NotImplementedError
