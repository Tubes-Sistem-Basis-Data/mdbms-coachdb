from __future__ import annotations

from dataclasses import dataclass

from common.catalog import DataType
from common.expression import ColumnRef
from common.storage import RID
from query_processor.errors import PlanError

Value = int | float | str | None


@dataclass(frozen=True)
class ColumnBinding:
    qualifier: str
    name: str
    type: DataType
    alternate_qualifiers: tuple[str, ...] = ()


@dataclass
class OutputSchema:
    columns: list[ColumnBinding]

    def index_of(self, ref: ColumnRef) -> int:
        """Cari posisi kolom; tolak yang hilang atau ambigu."""
        if not isinstance(ref, ColumnRef):
            raise PlanError("Plan tidak valid: referensi kolom harus berupa ColumnRef")
        matches = [
            i for i, column in enumerate(self.columns)
            if column.name == ref.column and (
                ref.table is None
                or ref.table == column.qualifier
                or ref.table in column.alternate_qualifiers
            )
        ]
        name = f"{ref.table}.{ref.column}" if ref.table else ref.column
        if not matches:
            raise PlanError(f"Plan tidak valid: kolom '{name}' tidak ada pada keluaran anak")
        if len(matches) > 1:
            raise PlanError(f"Plan tidak valid: kolom '{name}' ambigu pada keluaran anak")
        return matches[0]


@dataclass
class ExecRow:
    values: tuple[Value, ...]
    schema: OutputSchema
    rid: RID | None = None
