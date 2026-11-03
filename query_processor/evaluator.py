from __future__ import annotations

import operator
from dataclasses import dataclass
from math import isfinite

from common.catalog import DataType
from common.errors import SemanticError
from common.expression import (
    And, ArithOp, BinaryOp, ColumnRef, Comparison, CompOp, Expression, Literal,
)
from query_processor.errors import ExecutionError, PlanError
from query_processor.tuple import ExecRow, OutputSchema, Value


_MIN_INTEGER = -(1 << 63)
_MAX_INTEGER = (1 << 63) - 1
_NUMERIC = (DataType.INTEGER, DataType.FLOAT)
_TEXT = (DataType.CHAR, DataType.VARCHAR)
_ARITHMETIC = {
    ArithOp.ADD: operator.add,
    ArithOp.SUB: operator.sub,
    ArithOp.MUL: operator.mul,
    ArithOp.DIV: operator.truediv,
}
_COMPARISONS = {
    CompOp.EQ: operator.eq,
    CompOp.NE: operator.ne,
    CompOp.GT: operator.gt,
    CompOp.GE: operator.ge,
    CompOp.LT: operator.lt,
    CompOp.LE: operator.le,
}


@dataclass(frozen=True)
class _Evaluated:
    value: Value | bool
    type: DataType | None = None


class ExpressionEvaluator:
    """Evaluasi pohon ekspresi terhadap satu baris."""

    def eval(self, expr: Expression, row: ExecRow) -> Value | bool:
        if (
            not isinstance(row, ExecRow)
            or not isinstance(row.schema, OutputSchema)
            or not isinstance(row.schema.columns, list)
            or not isinstance(row.values, tuple)
            or len(row.values) != len(row.schema.columns)
        ):
            raise ExecutionError("Jumlah nilai baris tidak sesuai skema")
        try:
            return self._eval(expr, row, set()).value
        except RecursionError as exc:
            raise PlanError("Pohon ekspresi terlalu dalam") from exc

    def eval_bool(self, expr: Expression, row: ExecRow) -> bool:
        """Evaluasi kondisi tanpa konversi truthiness."""
        return self._as_bool(self.eval(expr, row))

    def _eval(self, expr: Expression, row: ExecRow, active: set[int]) -> _Evaluated:
        if id(expr) in active:
            raise PlanError("Pohon ekspresi mengandung siklus")
        active.add(id(expr))
        try:
            if isinstance(expr, Literal):
                return self._typed(expr.value, expr.type)
            if isinstance(expr, ColumnRef):
                position = row.schema.index_of(expr)
                return self._typed(row.values[position], row.schema.columns[position].type)
            if isinstance(expr, And):
                if not isinstance(expr.conditions, list) or not expr.conditions:
                    raise PlanError("AND harus berisi kondisi")
                for condition in expr.conditions:
                    if not self._as_bool(self._eval(condition, row, active).value):
                        return _Evaluated(False)
                return _Evaluated(True)
            if isinstance(expr, BinaryOp):
                if not isinstance(expr.op, ArithOp):
                    raise PlanError("Operator aritmetika tidak dikenal")
                left = self._eval(expr.left, row, active)
                right = self._eval(expr.right, row, active)
                return self._arithmetic(expr.op, left, right)
            if isinstance(expr, Comparison):
                if not isinstance(expr.op, CompOp):
                    raise PlanError("Operator perbandingan tidak dikenal")
                left = self._eval(expr.left, row, active)
                right = self._eval(expr.right, row, active)
                return _Evaluated(self._compare(expr.op, left, right))
            raise PlanError(f"Node ekspresi '{type(expr).__name__}' tidak didukung")
        finally:
            active.remove(id(expr))

    def _typed(self, value: Value, data_type: DataType) -> _Evaluated:
        if not isinstance(data_type, DataType):
            raise PlanError("Tipe nilai ekspresi tidak dikenal")
        if value is None:
            return _Evaluated(None, data_type)
        if data_type is DataType.INTEGER:
            if type(value) is not int:
                raise SemanticError("Nilai INTEGER harus berupa bilangan bulat, bukan boolean")
            if not _MIN_INTEGER <= value <= _MAX_INTEGER:
                raise ExecutionError("Nilai INTEGER melebihi batas 64-bit")
        elif data_type is DataType.FLOAT:
            if type(value) not in (int, float):
                raise SemanticError("Nilai FLOAT harus berupa angka")
            try:
                value = float(value)
            except OverflowError as exc:
                raise ExecutionError("Nilai FLOAT melebihi batas") from exc
            if not isfinite(value):
                raise ExecutionError("Nilai FLOAT harus berhingga")
        else:
            if type(value) is not str:
                raise SemanticError("Nilai CHAR/VARCHAR harus berupa teks")
            if data_type is DataType.CHAR:
                value = value.rstrip(" ")
        return _Evaluated(value, data_type)

    def _arithmetic(self, op: ArithOp, left: _Evaluated, right: _Evaluated) -> _Evaluated:
        if left.type not in _NUMERIC or right.type not in _NUMERIC:
            raise SemanticError("Operasi aritmetika hanya menerima angka")
        result_type = DataType.FLOAT if DataType.FLOAT in (left.type, right.type) else DataType.INTEGER
        if left.value is None or right.value is None:
            return _Evaluated(None, result_type)
        if op is ArithOp.DIV:
            if right.value == 0:
                raise ExecutionError("Pembagian dengan nol")
            if result_type is DataType.INTEGER:
                value = abs(left.value) // abs(right.value)
                if (left.value < 0) != (right.value < 0):
                    value = -value
                return self._typed(value, result_type)
        return self._typed(_ARITHMETIC[op](left.value, right.value), result_type)

    def _compare(self, op: CompOp, left: _Evaluated, right: _Evaluated) -> bool:
        if left.value is None or right.value is None:
            return False
        numeric = left.type in _NUMERIC and right.type in _NUMERIC
        text = left.type in _TEXT and right.type in _TEXT
        if not numeric and not text:
            raise SemanticError("Tipe operand perbandingan tidak kompatibel")
        left_value, right_value = left.value, right.value
        if text and DataType.CHAR in (left.type, right.type):
            left_value = left_value.rstrip(" ")
            right_value = right_value.rstrip(" ")
        return _COMPARISONS[op](left_value, right_value)

    def _as_bool(self, value: Value | bool) -> bool:
        if type(value) is not bool:
            raise PlanError("Kondisi harus menghasilkan boolean")
        return value
