"""Operator join (QP4)."""

from __future__ import annotations

from common.expression import Expression
from common.plan import JoinKind
from query_processor.context import ExecContext
from query_processor.evaluator import ExpressionEvaluator
from query_processor.operators.base import RowOperator
from query_processor.tuple import ExecRow, OutputSchema


class BlockNestedLoopJoinOp(RowOperator):
    """Gabungkan dua child dengan Block Nested Loop Join.

    Kolom bersama NATURAL JOIN dan skema output disiapkan translator.
    Child kiri jadi outer, child kanan jadi inner, sama dengan cost model QO.
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
        """Simpan kedua child dan konfigurasi join."""
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
        """Skema output, kolom bersama NATURAL JOIN cukup muncul sekali."""
        return self._schema

    def open(self, ctx: ExecContext) -> None:
        """Buka kedua child sebelum iterasi."""
        return None

    def next(self) -> ExecRow | None:
        """Baris hasil join berikutnya, None jika sudah habis."""
        return None

    def close(self) -> None:
        """Tutup kedua child, termasuk saat iterasi gagal."""
        return None
