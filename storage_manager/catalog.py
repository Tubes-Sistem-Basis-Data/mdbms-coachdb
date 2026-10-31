from __future__ import annotations

from typing import Optional

from common.catalog import ColumnSchema, ForeignKey, IndexInfo, TableSchema


class Catalog:
    def __init__(self, path: str) -> None:
        self._path = path
        self._tables: dict[str, TableSchema] = {}
        self._indexes: dict[str, IndexInfo] = {}

    def list_tables(self) -> list[str]:
        return []

    def get_table_schema(self, table: str) -> TableSchema:
        return TableSchema(name="", columns=[])

    def create_table(
        self,
        name: str,
        columns: tuple[ColumnSchema, ...],
        primary_key: tuple[str, ...],
        foreign_keys: tuple[ForeignKey, ...],
    ) -> TableSchema:
        return TableSchema(name=name, columns=list(columns), primary_key=list(primary_key), foreign_keys=list(foreign_keys))

    def register_index(self, info: IndexInfo) -> None:
        return None

    def unregister_index(self, name: str) -> None:
        return None

    def list_indexes(self, table: Optional[str] = None) -> list[IndexInfo]:
        return []

    def _load(self) -> None:
        return None

    def _persist(self) -> None:
        return None
