from __future__ import annotations

from dataclasses import dataclass

from common.catalog import DataType
from common.expression import ColumnRef
from common.storage import RID

type Value = int | float | str | None


@dataclass(frozen=True)
class ColumnBinding:
    qualifier: str
    name: str
    type: DataType


@dataclass
class OutputSchema:
    columns: list[ColumnBinding]

    def index_of(self, ref: ColumnRef) -> int:
        return 0


@dataclass
class ExecRow:
    values: tuple[Value, ...]
    schema: OutputSchema
    rid: RID | None = None
