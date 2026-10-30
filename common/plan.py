"""Hasil parsing dan node query plan. Dibuat QO, dieksekusi QP."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from common.catalog import IndexInfo
from common.expression import ColumnRef, CompOp, Expression, Literal


class QueryKind(Enum):
    """Jenis perintah SQL."""

    SELECT = "SELECT"
    INSERT = "INSERT"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    CREATE_INDEX = "CREATE_INDEX"
    TRANSACTION = "TRANSACTION"
    EXPLAIN = "EXPLAIN"


class TransactionCommand(Enum):
    """Perintah kontrol transaksi."""

    BEGIN = "BEGIN"
    COMMIT = "COMMIT"
    ABORT = "ABORT"


class JoinKind(Enum):
    """Jenis join."""

    CROSS = "CROSS"
    THETA = "THETA"
    NATURAL = "NATURAL"


@dataclass(kw_only=True)
class PlanNode:
    """Basis node plan. Semua field punya default supaya turunan mudah dibuat."""

    children: list[PlanNode] = field(default_factory=list)
    output_columns: list[ColumnRef] = field(default_factory=list)
    estimated_rows: float = 0.0
    estimated_cost: float = 0.0


@dataclass(kw_only=True)
class TableScan(PlanNode):
    """Baca seluruh tabel."""

    table: str


@dataclass(kw_only=True)
class IndexScan(PlanNode):
    """Baca lewat index. Kesamaan pakai `key`, rentang pakai `low`/`high`."""

    table: str
    index: IndexInfo
    op: CompOp
    key: object = None
    low: object = None
    high: object = None


@dataclass(kw_only=True)
class Filter(PlanNode):
    """Seleksi baris dengan kondisi."""

    condition: Expression


@dataclass(kw_only=True)
class Project(PlanNode):
    """Proyeksi kolom."""

    columns: list[ColumnRef]


@dataclass(kw_only=True)
class Join(PlanNode):
    """Join dua anak. Algoritma minimal BNLJ."""

    kind: JoinKind
    condition: Expression | None = None
    algorithm: str = "BNLJ"


@dataclass(kw_only=True)
class Sort(PlanNode):
    """Pengurutan berdasarkan satu kolom."""

    column: ColumnRef
    ascending: bool = True


@dataclass(kw_only=True)
class Insert(PlanNode):
    """Sisip satu baris."""

    table: str
    values: list[Literal]


@dataclass(kw_only=True)
class Update(PlanNode):
    """Ubah satu kolom pada baris hasil anak."""

    table: str
    column: str
    expression: Expression


@dataclass(kw_only=True)
class Delete(PlanNode):
    """Hapus baris hasil anak."""

    table: str


@dataclass(kw_only=True)
class CreateIndex(PlanNode):
    """Buat index baru."""

    index: IndexInfo


@dataclass
class ParsedQuery:
    """Hasil `QO.parse_query` / `QO.optimize_query`."""

    sql: str
    kind: QueryKind
    plan: PlanNode | None = None
    command: TransactionCommand | None = None
    estimated_cost: float = 0.0
