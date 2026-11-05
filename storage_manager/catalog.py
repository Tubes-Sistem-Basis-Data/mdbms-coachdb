from __future__ import annotations

import copy
import json
import os
import tempfile
from dataclasses import asdict
from enum import Enum
from typing import Optional

from common.catalog import ColumnSchema, DataType, ForeignKey, IndexInfo, IndexKind, TableSchema
from common.errors import IndexDefinitionError, SchemaViolationError, StorageError, TableNotFoundError
from storage_manager.serializer import RecordCodec


class Catalog:
    def __init__(self, path: str) -> None:
        self._path = path
        self._tables: dict[str, TableSchema] = {}
        self._indexes: dict[str, IndexInfo] = {}
        self._record_size: dict[str, int] = {}
        self._load()

    def list_tables(self) -> list[str]:
        return list(self._tables)

    def get_table_schema(self, table: str) -> TableSchema:
        if table not in self._tables:
            raise TableNotFoundError(f"Tabel tidak ditemukan: {table}", table)
        return copy.deepcopy(self._tables[table])

    def get_record_size(self, table: str) -> int:
        """Internal maximum record capacity in bytes; not part of TableSchema."""
        if table not in self._tables:
            raise TableNotFoundError(f"Tabel tidak ditemukan: {table}", table)
        return self._record_size[table]

    @staticmethod
    def _validate_schema(schema: TableSchema, tables: dict[str, TableSchema]) -> None:
        def fail(message: str) -> None:
            raise SchemaViolationError(message)

        if not isinstance(schema.name, str) or not schema.name or not schema.columns:
            fail("Nama tabel dan kolom wajib diisi")
        names = []
        for col in schema.columns:
            if not isinstance(col.name, str) or not col.name or col.name in names:
                fail("Nama kolom kosong atau duplikat")
            names.append(col.name)
            if not isinstance(col.type, DataType) or type(col.nullable) is not bool:
                fail(f"Definisi kolom tidak valid: {col.name}")
            if col.type in (DataType.CHAR, DataType.VARCHAR):
                if type(col.length) is not int or col.length <= 0:
                    fail(f"Panjang kolom harus positif: {col.name}")
            elif col.length is not None:
                fail(f"length hanya untuk CHAR/VARCHAR: {col.name}")
        if len(set(schema.primary_key)) != len(schema.primary_key) or any(k not in names for k in schema.primary_key):
            fail("Primary key harus menunjuk kolom unik yang tersedia")
        for fk in schema.foreign_keys:
            target = schema if fk.ref_table == schema.name else tables.get(fk.ref_table)
            if fk.column not in names or target is None or fk.ref_column not in target.column_names():
                fail("Referensi foreign key tidak ditemukan")
            source_col = schema.columns[names.index(fk.column)]
            target_col = target.columns[target.column_index(fk.ref_column)]
            if source_col.type != target_col.type:
                fail("Tipe kolom foreign key tidak cocok")

    def create_table(
        self,
        name: str,
        columns: tuple[ColumnSchema, ...],
        primary_key: tuple[str, ...],
        foreign_keys: tuple[ForeignKey, ...],
    ) -> TableSchema:
        if name in self._tables:
            raise SchemaViolationError(f"Tabel sudah ada: {name}")
        schema = copy.deepcopy(TableSchema(name, list(columns), list(primary_key), list(foreign_keys)))
        self._validate_schema(schema, self._tables)
        record_size = RecordCodec.max_record_size(schema)
        self._tables[name] = schema
        self._record_size[name] = record_size
        try:
            self._persist()
        except StorageError:
            del self._tables[name]
            del self._record_size[name]
            raise
        return copy.deepcopy(schema)

    def register_index(self, info: IndexInfo) -> None:
        schema = self.get_table_schema(info.table)
        if (not info.name or info.name in self._indexes or info.column not in schema.column_names()
                or not isinstance(info.kind, IndexKind)
                or any(i.column == info.column for i in schema.indexes)):
            raise IndexDefinitionError(f"Definisi index tidak valid: {info.name}")
        info = copy.deepcopy(info)
        self._indexes[info.name] = info
        self._tables[info.table].indexes.append(info)
        try:
            self._persist()
        except StorageError:
            del self._indexes[info.name]
            self._tables[info.table].indexes.pop()
            raise

    def unregister_index(self, name: str) -> None:
        if name not in self._indexes:
            raise IndexDefinitionError(f"Index tidak ditemukan: {name}")
        old_indexes = self._indexes.copy()
        info = self._indexes.pop(name)
        schema = self._tables[info.table]
        old_list = schema.indexes
        schema.indexes = [i for i in old_list if i.name != name]
        try:
            self._persist()
        except StorageError:
            self._indexes = old_indexes
            schema.indexes = old_list
            raise

    def list_indexes(self, table: Optional[str] = None) -> list[IndexInfo]:
        if table is not None:
            return self.get_table_schema(table).indexes
        return copy.deepcopy(list(self._indexes.values()))

    def _load(self) -> None:
        """Replace metadata only after the entire catalog has been validated."""
        try:
            with open(self._path, encoding="utf-8") as stream:
                data = json.load(stream)
        except FileNotFoundError:
            self._tables, self._indexes, self._record_size = {}, {}, {}
            return
        except (OSError, ValueError) as exc:
            raise StorageError(f"Gagal membaca katalog: {self._path}") from exc
        try:
            if data["version"] != 1:
                raise ValueError("Versi katalog tidak didukung")
            tables: dict[str, TableSchema] = {}
            indexes: dict[str, IndexInfo] = {}
            record_sizes: dict[str, int] = {}
            for item in data["tables"]:
                schema = TableSchema(
                    name=item["name"],
                    columns=[ColumnSchema(**{**c, "type": DataType(c["type"])}) for c in item["columns"]],
                    primary_key=item["primary_key"],
                    foreign_keys=[ForeignKey(**fk) for fk in item["foreign_keys"]],
                    indexes=[IndexInfo(**{**i, "kind": IndexKind(i["kind"])}) for i in item["indexes"]],
                )
                if schema.name in tables:
                    raise ValueError("Tabel duplikat")
                tables[schema.name] = schema
            for schema in tables.values():
                self._validate_schema(schema, tables)
                record_sizes[schema.name] = RecordCodec.max_record_size(schema)
                columns = set()
                for info in schema.indexes:
                    if (not info.name or info.name in indexes or info.table != schema.name
                            or info.column not in schema.column_names() or info.column in columns):
                        raise ValueError("Metadata index tidak valid")
                    indexes[info.name] = info
                    columns.add(info.column)
        except (KeyError, TypeError, ValueError, AttributeError, SchemaViolationError) as exc:
            raise StorageError(f"Katalog tidak valid: {self._path}") from exc
        self._tables, self._indexes, self._record_size = tables, indexes, record_sizes

    def _persist(self) -> None:
        """Write a complete JSON file, fsync, then atomically replace the old file."""
        temporary = None
        try:
            directory = os.path.dirname(os.path.abspath(self._path))
            os.makedirs(directory, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                             prefix=".catalog-", suffix=".tmp", delete=False) as stream:
                temporary = stream.name
                json.dump({"version": 1, "tables": [asdict(s) for s in self._tables.values()]},
                          stream, ensure_ascii=False, indent=2,
                          default=lambda value: value.value if isinstance(value, Enum) else value)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
        except (OSError, TypeError, ValueError) as exc:
            raise StorageError(f"Gagal menyimpan katalog: {self._path}") from exc
        finally:
            if temporary is not None and os.path.exists(temporary):
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
