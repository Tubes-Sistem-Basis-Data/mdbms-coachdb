"""Pohon ekspresi hasil parsing QO, dievaluasi oleh QP."""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from common.catalog import DataType

if TYPE_CHECKING:
    from common.plan import PlanNode


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
    """Referensi kolom. `table` selalu terisi setelah validasi QO.

    Jika sumbernya memakai alias (`TableScan.alias` atau `SubqueryScan.alias`), `table`
    berisi alias tersebut, bukan nama tabel asli.
    """

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


@dataclass
class Subquery(Expression):
    """Subquery skalar di dalam kondisi, mis. `WHERE ipk > (SELECT ...)`.

    `plan` harus menghasilkan tepat satu kolom dan paling banyak satu baris. Hanya
    subquery tak-berkorelasi: `plan` tidak boleh merujuk kolom dari query luar.
    """

    plan: PlanNode
