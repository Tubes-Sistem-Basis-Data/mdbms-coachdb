from __future__ import annotations

from typing import Optional

from common.types import ColumnSchema, ForeignKey, IndexInfo, TableSchema


class Catalog:
    def __init__(self, path: str) -> None:
        self._path = path
        self._tables: dict[str, TableSchema] = {}
        self._indexes: dict[str, IndexInfo] = {}

    def list_tables(self) -> list[str]:
        raise NotImplementedError

    def get_table_schema(self, table: str) -> TableSchema:
        raise NotImplementedError

    def create_table(
        self,
        name: str,
        columns: tuple[ColumnSchema, ...],
        primary_key: tuple[str, ...],
        foreign_keys: tuple[ForeignKey, ...],
    ) -> TableSchema:
        raise NotImplementedError

    def register_index(self, info: IndexInfo) -> None:
        raise NotImplementedError

    def unregister_index(self, name: str) -> None:
        raise NotImplementedError

    def list_indexes(self, table: Optional[str] = None) -> list[IndexInfo]:
        raise NotImplementedError

    def _load(self) -> None:
        raise NotImplementedError

    def _persist(self) -> None:
        raise NotImplementedError