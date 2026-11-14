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
    labels: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if self.labels is not None:
            self.output_names()

    def output_names(self) -> list[str]:
        """Nama tampilan tanpa mengubah binding kolom sumber."""
        if self.labels is None:
            return [column.name for column in self.columns]
        if (
            not isinstance(self.labels, tuple)
            or not isinstance(self.columns, list)
            or len(self.labels) != len(self.columns)
        ):
            raise PlanError("Label keluaran harus berupa tuple yang sejajar dengan kolom")
        if any(not isinstance(label, str) or not label.strip() for label in self.labels):
            raise PlanError("Label keluaran harus berupa string yang tidak kosong")
        return list(self.labels)

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
