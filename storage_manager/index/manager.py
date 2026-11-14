"""SM-10/SM-11: registry index dan pemeliharaannya.

Index tidak ikut di-snapshot. Metadata index disimpan di katalog, sedangkan
struktur in-memory dibangun ulang dari isi tabel saat startup/recovery lewat
``rebuild_all``. ``IndexManager`` memegang lock sendiri supaya method facade
tetap thread-safe tanpa pemanggil perlu memegang lock.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Callable, Iterator, Optional

from common.catalog import DataType, IndexInfo, IndexKind, TableSchema
from common.errors import IndexDefinitionError
from common.storage import RID, Row
from storage_manager.types import Value

if TYPE_CHECKING:
    from storage_manager.catalog import Catalog


class Index(ABC):

    def __init__(self, table: str, column: str) -> None:
        self.table: str = table
        self.column: str = column

    @abstractmethod
    def insert(self, key: Value, rid: RID) -> None:
        ...

    @abstractmethod
    def delete(self, key: Value, rid: RID) -> None:
        ...

    @abstractmethod
    def search(self, key: Value) -> list[RID]:
        ...

    @abstractmethod
    def range(self, low: Optional[Value], high: Optional[Value]) -> list[RID]:
        ...

    @abstractmethod
    def height(self) -> int:
        ...


class IndexManager:
    def __init__(
        self,
        catalog: Catalog,
        scan_fn: Callable[[str], Iterator[Row]],
    ) -> None:
        self._catalog = catalog
        self._scan_fn = scan_fn
        self._indexes: dict[str, Index] = {}
        self._rid_keys: dict[str, dict[RID, Value]] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ create

    def create_index(
        self, name: str, table: str, column: str, kind: IndexKind
    ) -> IndexInfo:
        with self._lock:
            schema = self._catalog.get_table_schema(table)
            if column not in schema.column_names():
                raise IndexDefinitionError(
                    f"Kolom {column} tidak ada di tabel {table}"
                )
            if not isinstance(kind, IndexKind):
                raise IndexDefinitionError(f"Jenis index tidak valid: {kind!r}")
            if (
                name in self._indexes
                or any(info.name == name for info in self._catalog.list_indexes())
                or any(info.column == column for info in schema.indexes)
            ):
                raise IndexDefinitionError(f"Definisi index tidak valid: {name}")
            info = IndexInfo(name=name, table=table, column=column, kind=kind)

        # Scan tabel di luar lock manager: scan memakai lock facade, sedangkan
        # lookup memegang lock facade lalu meminta lock manager. Menahan lock
        # manager saat scan bisa menimbulkan lock-order inversion.
        index, rid_keys = self._build(info)

        with self._lock:
            # register_index memvalidasi ulang nama/kolom, jadi pembangunan
            # paralel tidak bisa menghasilkan metadata ganda.
            self._catalog.register_index(info)
            self._indexes[name] = index
            self._rid_keys[name] = rid_keys
        return IndexInfo(name=name, table=table, column=column, kind=kind)

    def _make_index(self, info: IndexInfo) -> Index:
        # Import lokal untuk menghindari circular import: bplus_tree dan
        # hash_index sama-sama meng-import Index dari modul ini.
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
        is_char = schema.columns[position].type is DataType.CHAR
        rid_keys: dict[RID, Value] = {}
        for row in self._scan_fn(info.table):
            key = row.values[position]
            if key is None:
                continue
            if is_char and isinstance(key, str):
                key = key.rstrip(" ")
            index.insert(key, row.rid)
            rid_keys[row.rid] = key
        return index, rid_keys

    # ------------------------------------------------------------------ lookup

    def lookup(self, name: str, key: Value) -> list[RID]:
        with self._lock:
            index = self._get(name)
            schema = self._catalog.get_table_schema(index.table)
            normalized = self._normalize(schema, index.column, key)
            if normalized is None:
                return []
            return index.search(normalized)

    def range(
        self, name: str, low: Optional[Value], high: Optional[Value]
    ) -> list[RID]:
        with self._lock:
            index = self._get(name)
            from storage_manager.index.hash_index import HashIndex

            if isinstance(index, HashIndex):
                raise IndexDefinitionError(
                    f"Index Hash {name} hanya mendukung pencarian kesamaan"
                )
            schema = self._catalog.get_table_schema(index.table)
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

    # ----------------------------------------------------------------- maintain

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
                if old_key is not None and old_key != new_key:
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

        # Bangun di luar lock manager supaya scan tidak menahan lock ini.
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

    # ------------------------------------------------------------------ helper

    def _apply_insert(
        self, name: str, index: Index, rid: RID, key: Optional[Value]
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
    def _normalize(schema: TableSchema, column: str, value: Value) -> Value:
        """CHAR dipadding spasi saat encode, jadi key index di-rstrip."""
        if value is None:
            return None
        position = schema.column_index(column)
        if schema.columns[position].type is DataType.CHAR and isinstance(value, str):
            return value.rstrip(" ")
        return value
