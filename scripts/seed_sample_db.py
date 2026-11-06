"""SM-14: deterministic in-memory sample and portable fixture, not disk pages."""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import random
import tempfile
from typing import Iterator

from common import config
from common.catalog import ColumnSchema, DataType, ForeignKey
from common.errors import SchemaViolationError, StorageError, TableNotFoundError
from common.storage import RID, Row
from storage_manager.catalog import Catalog
from storage_manager.page import SlottedPage
from storage_manager.serializer import RecordCodec
from storage_manager.stats import StatisticsManager

FIXTURE_FILE = 'sample-db.json'

class SampleDatabase:
    def __init__(self, catalog: Catalog, rows: dict[str, list[list]]) -> None:
        self._catalog = catalog
        self._rows: dict[str, list[Row]] = {}
        if set(rows) != set(catalog.list_tables()):
            raise SchemaViolationError('Tabel fixture tidak sesuai katalog')
        for table in catalog.list_tables():
            schema = catalog.get_table_schema(table)
            capacity = SlottedPage.capacity(catalog.get_record_size(table))
            if capacity <= 0:
                raise StorageError(f'Record tidak muat satu blok: {table}')
            keys = set()
            result = []
            for index, values in enumerate(rows[table]):
                RecordCodec.validate(schema, tuple(values))
                key = tuple(values[schema.column_index(c)] for c in schema.primary_key)
                if schema.primary_key and (None in key or key in keys):
                    raise SchemaViolationError(f'Primary key tidak valid: {table}')
                keys.add(key)
                result.append(Row(RID(table, index // capacity, index % capacity), copy.deepcopy(list(values))))
            self._rows[table] = result
        for table in catalog.list_tables():
            schema = catalog.get_table_schema(table)
            for fk in schema.foreign_keys:
                target = catalog.get_table_schema(fk.ref_table)
                allowed = {r.values[target.column_index(fk.ref_column)] for r in self._rows[fk.ref_table]}
                for row in self._rows[table]:
                    value = row.values[schema.column_index(fk.column)]
                    if value is not None and value not in allowed:
                        raise SchemaViolationError(f'Foreign key tidak valid: {table}.{fk.column}')
        self._stats = StatisticsManager(catalog, self.scan)

    def list_tables(self):
        return self._catalog.list_tables()

    def get_schema(self, table):
        return self._catalog.get_table_schema(table)

    def list_indexes(self, table=None):
        return self._catalog.list_indexes(table)

    def get_stats(self, table):
        return self._stats.get_stats(table)

    def scan(self, table: str) -> Iterator[Row]:
        self.get_schema(table)
        return iter(copy.deepcopy(self._rows[table]))

    def fetch(self, table: str, rid: RID) -> Row | None:
        self.get_schema(table)
        if rid.table != table:
            raise StorageError('RID tidak sesuai tabel')
        return next((row for row in self.scan(table) if row.rid == rid), None)


class SampleDatabaseSeeder:
    SEED: int = 3140

    def build(self) -> SampleDatabase:
        catalog = Catalog(None)
        self._create_tables(catalog)
        return SampleDatabase(catalog, {t: [list(r) for r in self._generate_rows(t)]
                                        for t in catalog.list_tables()})

    def seed(self, data_dir: str, force: bool) -> None:
        """Write only sample-db.json; never delete/overwrite actual database files."""
        db = self.build()
        directory = Path(data_dir)
        path = directory / FIXTURE_FILE
        temporary = None
        try:
            directory.mkdir(parents=True, exist_ok=True)
            if path.exists() and not force:
                raise StorageError(f'Fixture sudah ada: {path}; gunakan --force untuk mengganti fixture')
            document = {'format': 'coachdb-sample-v1', 'seed': self.SEED,
                        'rows': {t: [r.values for r in db.scan(t)] for t in db.list_tables()}}
            with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=directory,
                                             prefix='.sample-', delete=False) as stream:
                temporary = stream.name
                json.dump(document, stream, indent=2, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            if force:
                os.replace(temporary, path)
            else:
                # Atomic no-clobber publication even if another seeder races us.
                os.link(temporary, path)
        except OSError as exc:
            raise StorageError(f'Gagal menyimpan fixture: {path}') from exc
        finally:
            if temporary is not None and os.path.exists(temporary):
                os.unlink(temporary)

    def load(self, data_dir: str) -> SampleDatabase:
        try:
            with open(Path(data_dir) / FIXTURE_FILE, encoding='utf-8') as stream:
                document = json.load(stream)
            if document['format'] != 'coachdb-sample-v1' or document['seed'] != self.SEED:
                raise ValueError('Format/seed fixture tidak didukung')
            catalog = Catalog(None)
            self._create_tables(catalog)
            return SampleDatabase(catalog, document['rows'])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise StorageError('Fixture tidak valid atau tidak dapat dibaca') from exc

    def load_into(self, storage) -> None:
        db = self.build()
        for table in db.list_tables():
            if storage.get_schema(table) != db.get_schema(table) or list(storage.scan(table)):
                raise StorageError(f'Tujuan harus kosong dengan skema contoh yang sama: {table}')
        for table in db.list_tables():
            values = [r.values for r in db.scan(table)]
            if storage.bulk_load(table, values) != len(values):
                raise StorageError(f'bulk_load belum berfungsi atau jumlah baris salah: {table}')
            actual = [r.values for r in storage.scan(table)]
            if sorted(map(tuple, actual)) != sorted(map(tuple, values)):
                raise StorageError(f'Hasil scan setelah bulk_load tidak sesuai: {table}')
        storage.flush()

    def _create_tables(self, catalog: Catalog) -> None:
        def col(name, kind, length=None):
            return ColumnSchema(name, kind, length, nullable=False)
        catalog.create_table('student', (
            col('id', DataType.INTEGER), col('name', DataType.VARCHAR, 50),
            col('department', DataType.CHAR, 8), col('gpa', DataType.FLOAT)), ('id',), ())
        catalog.create_table('course', (
            col('id', DataType.INTEGER), col('name', DataType.VARCHAR, 60),
            col('credits', DataType.INTEGER)), ('id',), ())
        catalog.create_table('enroll', (
            col('student_id', DataType.INTEGER), col('course_id', DataType.INTEGER),
            col('grade', DataType.FLOAT)), ('student_id', 'course_id'), (
                ForeignKey('student_id', 'student', 'id'), ForeignKey('course_id', 'course', 'id')))

    def _generate_rows(self, table: str) -> list[tuple]:
        # Local RNG: repeatable independently of call order and global randomness.
        rng = random.Random(self.SEED)
        if table == 'student':
            return [(i, f'Student {i:03d}', ('CS', 'MATH', 'PHYS')[i % 3],
                     round(rng.uniform(2, 4), 2)) for i in range(1, 61)]
        if table == 'course':
            return [(i, f'Course {i:03d}', rng.choice((2, 3, 4))) for i in range(1, 51)]
        if table == 'enroll':
            pairs = rng.sample([(s, c) for s in range(1, 61) for c in range(1, 51)], 200)
            return [(s, c, round(rng.uniform(0, 4), 2)) for s, c in pairs]
        raise TableNotFoundError(f'Tabel contoh tidak dikenal: {table}', table)


def main() -> None:
    parser = argparse.ArgumentParser(description='Buat fixture contoh untuk database in-memory (bukan page disk SM).')
    parser.add_argument('--data-dir', default=config.DATA_DIR)
    parser.add_argument('--force', action='store_true', help='ganti sample-db.json saja')
    args = parser.parse_args()
    try:
        SampleDatabaseSeeder().seed(args.data_dir, args.force)
    except StorageError as exc:
        parser.exit(1, f'ERROR: {exc}\n')
    print(f'Fixture tersimpan: {Path(args.data_dir) / FIXTURE_FILE} (60 student, 50 course, 200 enroll)')


if __name__ == '__main__':
    main()
