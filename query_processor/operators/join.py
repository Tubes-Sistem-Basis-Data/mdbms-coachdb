"""Operator join (QP4)."""

from __future__ import annotations

from common.catalog import DataType
from common.expression import Expression
from common.plan import JoinKind
from query_processor.context import ExecContext
from query_processor.errors import ExecutionError, PlanError
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
        self._evaluator = evaluator if evaluator is not None else ExpressionEvaluator()
        self._shared_columns = shared_columns
        if block_size is not None and (type(block_size) is not int or block_size <= 0):
            raise PlanError("Ukuran blok JOIN harus berupa bilangan bulat positif")
        self._block_size = block_size if block_size is not None else 1

        if not isinstance(join_kind, JoinKind):
            raise PlanError("Jenis JOIN pada plan tidak dikenal")
        if join_kind is JoinKind.THETA and condition is None:
            raise PlanError("JOIN ON harus memiliki kondisi")
        if join_kind in (JoinKind.CROSS, JoinKind.NATURAL) and condition is not None:
            raise PlanError("Kondisi JOIN hanya berlaku untuk JOIN ON")
        if join_kind is not JoinKind.NATURAL and shared_columns:
            raise PlanError("Kolom bersama hanya berlaku untuk NATURAL JOIN")

        if any(
            not isinstance(pair, tuple)
            or len(pair) != 2
            or any(type(pos) is not int for pos in pair)
            for pair in shared_columns
        ):
            raise PlanError("Pasangan kolom NATURAL JOIN tidak valid")

        left_positions = [left_pos for left_pos, _ in shared_columns]
        right_positions = [right_pos for _, right_pos in shared_columns]
        if len(set(left_positions)) != len(left_positions) or len(set(right_positions)) != len(right_positions):
            raise PlanError("Pasangan kolom NATURAL JOIN tidak boleh berulang")
        if any(not 0 <= pos < len(left.schema.columns) for pos in left_positions):
            raise PlanError("Posisi kolom kiri NATURAL JOIN di luar skema")
        if any(not 0 <= pos < len(right.schema.columns) for pos in right_positions):
            raise PlanError("Posisi kolom kanan NATURAL JOIN di luar skema")
        if join_kind is JoinKind.NATURAL and any(
            left.schema.columns[left_pos].name != right.schema.columns[right_pos].name
            for left_pos, right_pos in shared_columns
        ):
            raise PlanError("Kolom NATURAL JOIN harus memiliki nama yang sama")

        expected_width = len(left.schema.columns) + len(right.schema.columns)
        if join_kind is JoinKind.NATURAL:
            expected_width -= len(shared_columns)
        if len(schema.columns) != expected_width:
            raise PlanError("Skema hasil JOIN tidak sesuai dengan skema anak")

        right_shared_positions = set(right_positions)
        self._right_output_positions = tuple(
            pos for pos in range(len(right.schema.columns)) if pos not in right_shared_positions
        )
        self._combined_schema = OutputSchema(left.schema.columns + right.schema.columns)
        self._left_open = False
        self._right_open = False
        self._opened = False
        self._inner_rows: list[ExecRow] = []
        self._left_block: list[ExecRow] = []
        self._left_block_pos = 0
        self._inner_pos = 0
        self._left_exhausted = False

    @property
    def schema(self) -> OutputSchema:
        """Skema output, kolom bersama NATURAL JOIN cukup muncul sekali."""
        return self._schema

    def open(self, ctx: ExecContext) -> None:
        """Buka child dan materialisasikan input dalam satu kali."""
        if self._left_open or self._right_open or self._opened:
            self.close()

        self._inner_rows = []
        self._left_block = []
        self._left_block_pos = 0
        self._inner_pos = 0
        self._left_exhausted = False

        try:
            self._left_open = True
            self._left.open(ctx)
            self._right_open = True
            self._right.open(ctx)
            while (row := self._right.next()) is not None:
                self._validate_row(row, self._right)
                self._inner_rows.append(row)
            self._right.close()
            self._right_open = False
            self._opened = True
        except BaseException:
            try:
                self.close()
            except BaseException:
                pass
            raise

    def next(self) -> ExecRow | None:
        """Return the next matching row in left-major, then right order."""
        if not self._opened or not self._inner_rows:
            return None

        while True:
            if self._left_block_pos >= len(self._left_block):
                if not self._load_left_block():
                    return None

            if self._inner_pos >= len(self._inner_rows):
                self._left_block_pos += 1
                self._inner_pos = 0
                continue

            left_row = self._left_block[self._left_block_pos]
            right_row = self._inner_rows[self._inner_pos]
            self._inner_pos += 1

            if self._join_kind is JoinKind.NATURAL:
                if not self._natural_match(left_row, right_row):
                    continue
                values = left_row.values + tuple(
                    right_row.values[pos] for pos in self._right_output_positions
                )
            else:
                values = left_row.values + right_row.values
                if self._condition is not None:
                    candidate = ExecRow(values, self._combined_schema)
                    if not self._evaluator.eval_bool(self._condition, candidate):
                        continue

            return ExecRow(values, self._schema, rid=None)

    def close(self) -> None:
        """Close all opened children and release materialized row buffers."""
        right_open, left_open = self._right_open, self._left_open
        self._right_open = False
        self._left_open = False
        self._opened = False
        self._inner_rows = []
        self._left_block = []
        self._left_block_pos = 0
        self._inner_pos = 0
        self._left_exhausted = True

        first_error: BaseException | None = None
        if right_open:
            try:
                self._right.close()
            except BaseException as exc:
                first_error = exc
        if left_open:
            try:
                self._left.close()
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error

    def _load_left_block(self) -> bool:
        """Read the next bounded block from the outer input."""
        self._left_block = []
        self._left_block_pos = 0
        self._inner_pos = 0
        while len(self._left_block) < self._block_size and not self._left_exhausted:
            row = self._left.next()
            if row is None:
                self._left_exhausted = True
                break
            self._validate_row(row, self._left)
            self._left_block.append(row)
        return bool(self._left_block)

    @staticmethod
    def _validate_row(row: ExecRow, child: RowOperator) -> None:
        if (
            not isinstance(row, ExecRow)
            or not isinstance(row.values, tuple)
            or not isinstance(row.schema, OutputSchema)
            or len(row.values) != len(child.schema.columns)
            or len(row.schema.columns) != len(child.schema.columns)
        ):
            raise ExecutionError("Jumlah nilai baris JOIN tidak sesuai skema anak")

    def _natural_match(self, left_row: ExecRow, right_row: ExecRow) -> bool:
        """Compare every shared column; NULL values never match."""
        for left_pos, right_pos in self._shared_columns:
            left_value = left_row.values[left_pos]
            right_value = right_row.values[right_pos]
            if left_value is None or right_value is None:
                return False

            left_type = self._left.schema.columns[left_pos].type
            right_type = self._right.schema.columns[right_pos].type
            if DataType.CHAR in (left_type, right_type):
                left_value = left_value.rstrip(" ")
                right_value = right_value.rstrip(" ")
            if left_value != right_value:
                return False
        return True
