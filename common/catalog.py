"""Tipe data katalog: skema tabel, index, dan statistik."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class DataType(Enum):
    """Tipe kolom yang didukung."""

    INTEGER = "INTEGER"
    FLOAT = "FLOAT"
    CHAR = "CHAR"
    VARCHAR = "VARCHAR"


class IndexKind(Enum):
    """Jenis struktur index."""

    HASH = "HASH"
    BPLUS_TREE = "BPLUS_TREE"


@dataclass
class ColumnSchema:
    """Definisi satu kolom. `length` dipakai untuk CHAR/VARCHAR."""

    name: str
    type: DataType
    length: int | None = None
    nullable: bool = True


@dataclass
class ForeignKey:
    """Relasi kolom ke kolom tabel lain."""

    column: str
    ref_table: str
    ref_column: str


@dataclass
class IndexInfo:
    """Metadata index pada satu kolom tabel."""

    name: str
    table: str
    column: str
    kind: IndexKind = IndexKind.BPLUS_TREE


@dataclass
class TableSchema:
    """Skema tabel, disediakan SM dan dipakai QO serta QP."""

    name: str
    columns: list[ColumnSchema]
    primary_key: list[str] = field(default_factory=list)
    foreign_keys: list[ForeignKey] = field(default_factory=list)
    indexes: list[IndexInfo] = field(default_factory=list)

    def column_names(self) -> list[str]:
        """Daftar nama kolom sesuai urutan."""
        return [c.name for c in self.columns]

    def column_index(self, name: str) -> int:
        """Posisi kolom dalam baris. Raise KeyError jika tidak ada."""
        for i, c in enumerate(self.columns):
            if c.name == name:
                return i
        raise KeyError(name)


@dataclass
class TableStats:
    """Statistik tabel untuk estimasi biaya QO.

    n_r: jumlah tuple, b_r: jumlah blok, l_r: ukuran tuple (byte),
    f_r: blocking factor, distinct: V(A, r) per kolom.
    """

    table: str
    n_r: int
    b_r: int
    l_r: float
    f_r: int
    distinct: dict[str, int]
