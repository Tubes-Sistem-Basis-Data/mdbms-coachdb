"""Pohon ekspresi hasil parsing QO, dievaluasi oleh QP."""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass
from enum import Enum

from common.catalog import DataType


class Expression(ABC):
    """Basis semua node ekspresi."""


class ArithOp(Enum):
    """Operator aritmetika."""

    ADD = "+"
    SUB = "-"
    MUL = "*"
    DIV = "/"


class CompOp(Enum):
    """Operator perbandingan."""

    EQ = "="
    NE = "<>"
    GT = ">"
    GE = ">="
    LT = "<"
    LE = "<="


@dataclass
class ColumnRef(Expression):
    """Referensi kolom. `table` selalu terisi setelah validasi QO."""

    table: str | None
    column: str


@dataclass
class Literal(Expression):
    """Nilai konstan beserta tipenya."""

    value: int | float | str | None
    type: DataType


@dataclass
class BinaryOp(Expression):
    """Operasi aritmetika dua operand."""

    op: ArithOp
    left: Expression
    right: Expression


@dataclass
class Comparison(Expression):
    """Perbandingan dua ekspresi."""

    op: CompOp
    left: Expression
    right: Expression


@dataclass
class And(Expression):
    """Konjungsi beberapa kondisi."""

    conditions: list[Expression]
