from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any, cast

from common.catalog import DataType, IndexInfo, IndexKind, TableSchema
from common.errors import IndexDefinitionError
from common.storage import RID, Row
from storage_manager.index.base import Index
from storage_manager.types import Value

if TYPE_CHECKING:
    from storage_manager.catalog import Catalog


class IndexManager:
    def __init__(
        self,
        catalog: Catalog,
        scan_fn: Callable[[str], Iterator[Row]],
    ) -> None:
        self._catalog: Catalog = catalog
        self._scan_fn: Callable[[str], Iterator[Row]] = scan_fn
        self._indexes: dict[str, Index] = {}
        self._rid_keys: dict[str, dict[RID, Value]] = {}
        self._lock: Any = threading.RLock()

    def create_index(
        self, name: str, table: str, column: str, kind: IndexKind
    ) -> IndexInfo:
        with self._lock:
            schema = self._catalog.get_table_schema(table)
            if column not in schema.column_names():
                raise IndexDefinitionError(
                    f"Kolom {column} tidak ada di tabel {table}"
                )
            if (
                not name
                or not isinstance(kind, IndexKind)
                or name in self._indexes
                or any(info.name == name for info in self._catalog.list_indexes())
                or any(info.column == column for info in schema.indexes)
            ):
                raise IndexDefinitionError(f"Definisi index tidak valid: {name}")
            info = IndexInfo(name=name, table=table, column=column, kind=kind)

        index, rid_keys = self._build(info)

        with self._lock:
            self._catalog.register_index(info)
            self._indexes[name] = index
            self._rid_keys[name] = rid_keys
        return IndexInfo(name=name, table=table, column=column, kind=kind)

    def _make_index(self, info: IndexInfo) -> Index:
        from storage_manager.index.bplus_tree import BPlusTree
        from storage_manager.index.hash_index import HashIndex

        if info.kind is IndexKind.BPLUS_TREE:
            return BPlusTree(info.table, info.column)
        if info.kind is IndexKind.HASH:
            return HashIndex(info.table, info.column)
        raise IndexDefinitionError(f"Jenis index tidak didukung: {info.kind!r}")

    def _build(self, info: IndexInfo) -> tuple[Index, dict[RID, Value]]:
        index = self._make_index(info)
        schema = self._catalog.get_table_schema(info.table)
        position = schema.column_index(info.column)
        rid_keys: dict[RID, Value] = {}
        for row in self._scan_fn(info.table):
            key = cast(Value, row.values[position])
            key = self._normalize(schema, info.column, key)
            if key is None:
                continue
            index.insert(key, row.rid)
            rid_keys[row.rid] = key
        return index, rid_keys

    def lookup(self, name: str, key: Value) -> list[RID]:
        with self._lock:
            index = self._get(name)
            schema = self._catalog.get_table_schema(index.table)
            if not self._is_type_compatible(schema, index.column, key):
                return []
            normalized = self._normalize(schema, index.column, key)
            if normalized is None:
                return []
            return index.search(normalized)

    def range(
        self, name: str, low: Value | None, high: Value | None
    ) -> list[RID]:
        with self._lock:
            index = self._get(name)
            from storage_manager.index.hash_index import HashIndex

            if isinstance(index, HashIndex):
                raise IndexDefinitionError(
                    f"Index Hash {name} hanya mendukung pencarian kesamaan"
                )
            schema = self._catalog.get_table_schema(index.table)
            if low is not None and not self._is_type_compatible(schema, index.column, low):
                return []
            if high is not None and not self._is_type_compatible(schema, index.column, high):
                return []
            normalized_low = self._normalize(schema, index.column, low)
            normalized_high = self._normalize(schema, index.column, high)
            return index.range(normalized_low, normalized_high)

    def height(self, name: str) -> int:
        with self._lock:
            return self._get(name).height()

    def _get(self, name: str) -> Index:
        index = self._indexes.get(name)
        if index is None:
            raise IndexDefinitionError(f"Index tidak ditemukan: {name}")
        return index

    def on_insert(self, table: str, values: tuple[Value, ...], rid: RID) -> None:
        with self._lock:
            schema = self._catalog.get_table_schema(table)
            for info in schema.indexes:
                index = self._indexes.get(info.name)
                if index is None:
                    continue
                position = self._position(schema, info.column)
                key = self._normalize(schema, info.column, values[position])
                self._apply_insert(info.name, index, rid, key)
        return None

    def on_delete(self, table: str, values: tuple[Value, ...], rid: RID) -> None:
        with self._lock:
            schema = self._catalog.get_table_schema(table)
            for info in schema.indexes:
                index = self._indexes.get(info.name)
                if index is None:
                    continue
                mapping = self._rid_keys.setdefault(info.name, {})
                key = mapping.pop(rid, None)
                if key is None:
                    position = self._position(schema, info.column)
                    key = self._normalize(schema, info.column, values[position])
                if key is not None:
                    index.delete(key, rid)
        return None

    def on_update(
        self,
        table: str,
        old: tuple[Value, ...],
        new: tuple[Value, ...],
        rid: RID,
    ) -> None:
        with self._lock:
            schema = self._catalog.get_table_schema(table)
            for info in schema.indexes:
                index = self._indexes.get(info.name)
                if index is None:
                    continue
                position = self._position(schema, info.column)
                new_key = self._normalize(schema, info.column, new[position])
                mapping = self._rid_keys.setdefault(info.name, {})
                old_key = mapping.get(rid)
                if old_key is None:
                    old_key = self._normalize(schema, info.column, old[position])
                if old_key == new_key:
                    continue
                if old_key is not None:
                    index.delete(old_key, rid)
                if new_key is None:
                    mapping.pop(rid, None)
                else:
                    index.insert(new_key, rid)
                    mapping[rid] = new_key
        return None

    def rebuild_all(self) -> None:
        with self._lock:
            infos = [
                info
                for table in self._catalog.list_tables()
                for info in self._catalog.list_indexes(table)
            ]

        indexes: dict[str, Index] = {}
        rid_keys: dict[str, dict[RID, Value]] = {}
        for info in infos:
            index, keys = self._build(info)
            indexes[info.name] = index
            rid_keys[info.name] = keys

        with self._lock:
            self._indexes = indexes
            self._rid_keys = rid_keys
        return None

    def _apply_insert(
        self, name: str, index: Index, rid: RID, key: Value
    ) -> None:
        mapping = self._rid_keys.setdefault(name, {})
        previous = mapping.get(rid)
        if previous is not None and previous != key:
            index.delete(previous, rid)
        if key is None:
            mapping.pop(rid, None)
            return
        index.insert(key, rid)
        mapping[rid] = key

    @staticmethod
    def _position(schema: TableSchema, column: str) -> int:
        return schema.column_index(column)

    @staticmethod
    def _is_type_compatible(schema: TableSchema, column: str, value: Value) -> bool:
        if value is None:
            return True
        position = schema.column_index(column)
        col_type = schema.columns[position].type
        if col_type in (DataType.INTEGER, DataType.FLOAT):
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if col_type in (DataType.CHAR, DataType.VARCHAR):
            return isinstance(value, str)
        return True

    @staticmethod
    def _normalize(schema: TableSchema, column: str, value: Value) -> Value:
        if value is None:
            return None
        position = schema.column_index(column)
        if schema.columns[position].type is DataType.CHAR and isinstance(value, str):
            return value.rstrip(" ")
        return value
