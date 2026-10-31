from __future__ import annotations

from typing import Union

from common.catalog import (
    ColumnSchema,
    DataType,
    ForeignKey,
    IndexInfo,
    IndexKind,
    TableSchema,
    TableStats,
)
from common.storage import RID, Row

Value = Union[int, float, str, None]
LSN = int

__all__ = [
    "Value", "LSN",
    "ColumnSchema", "DataType", "ForeignKey", "IndexInfo", "IndexKind",
    "TableSchema", "TableStats",
    "RID", "Row",
]