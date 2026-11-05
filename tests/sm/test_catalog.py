from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import (ColumnSchema, DataType, ForeignKey, IndexInfo, IndexKind,
                    IndexDefinitionError, SchemaViolationError, StorageError, TableNotFoundError)
from storage_manager.catalog import Catalog
from storage_manager.facade import StorageManager


class TestCatalog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'catalog.json'
        self.catalog = Catalog(str(self.path))

    def create_student(self):
        return self.catalog.create_table('student', (
            ColumnSchema('id', DataType.INTEGER, nullable=False),
            ColumnSchema('name', DataType.VARCHAR, length=50),
            ColumnSchema('grade', DataType.FLOAT),
            ColumnSchema('code', DataType.CHAR, length=4),
        ), ('id',), ())

    def test_empty_and_missing(self):
        self.assertEqual(self.catalog.list_tables(), [])
        for operation in (self.catalog.get_table_schema, self.catalog.list_indexes):
            with self.assertRaises(TableNotFoundError):
                operation('missing')

    def test_schema_round_trip_and_foreign_key(self):
        schema = self.create_student()
        self.catalog.create_table('enroll', (ColumnSchema('student_id', DataType.INTEGER),),
                                  ('student_id',), (ForeignKey('student_id', 'student', 'id'),))
        restored = Catalog(str(self.path))
        self.assertEqual(restored.list_tables(), ['student', 'enroll'])
        self.assertEqual(restored.get_table_schema('student'), schema)
        self.assertEqual(restored.get_table_schema('enroll').foreign_keys,
                         [ForeignKey('student_id', 'student', 'id')])
        self.assertNotIn('record_size', vars(schema))

    def test_defensive_copies(self):
        schema = self.create_student()
        schema.columns.clear()
        result = self.catalog.get_table_schema('student')
        result.columns[0].name = 'changed'
        self.assertEqual(self.catalog.get_table_schema('student').column_names(), ['id', 'name', 'grade', 'code'])

    def test_indexes_stay_consistent_and_persist(self):
        self.create_student()
        info = IndexInfo('idx', 'student', 'id', IndexKind.HASH)
        self.catalog.register_index(info)
        info.column = 'changed'
        restored = Catalog(str(self.path))
        self.assertEqual(restored.list_indexes('student'), restored.get_table_schema('student').indexes)
        self.assertEqual(restored.list_indexes()[0].kind, IndexKind.HASH)
        result = restored.list_indexes()
        result[0].name = 'changed'
        restored.unregister_index('idx')
        self.assertEqual(Catalog(str(self.path)).get_table_schema('student').indexes, [])

    def test_invalid_index(self):
        self.create_student()
        self.catalog.register_index(IndexInfo('idx', 'student', 'id'))
        for info in (IndexInfo('idx', 'student', 'name'), IndexInfo('other', 'student', 'id'),
                     IndexInfo('bad', 'student', 'unknown')):
            with self.assertRaises(IndexDefinitionError):
                self.catalog.register_index(info)
        with self.assertRaises(TableNotFoundError):
            self.catalog.register_index(IndexInfo('no', 'missing', 'id'))
        with self.assertRaises(IndexDefinitionError):
            self.catalog.unregister_index('missing')

    def test_invalid_schema_leaves_catalog_unchanged(self):
        cases = [
            ((), (), ()),
            ((ColumnSchema('id', DataType.INTEGER),) * 2, (), ()),
            ((ColumnSchema('id', DataType.INTEGER),), ('missing',), ()),
            ((ColumnSchema('id', DataType.VARCHAR, length=0),), (), ()),
            ((ColumnSchema('id', DataType.INTEGER),), (), (ForeignKey('id', 'missing', 'id'),)),
        ]
        for columns, pk, fk in cases:
            with self.subTest(columns=columns), self.assertRaises(SchemaViolationError):
                self.catalog.create_table('bad', columns, pk, fk)
        self.assertEqual(self.catalog.list_tables(), [])
        self.create_student()
        with self.assertRaises(SchemaViolationError):
            self.create_student()

    def test_failed_write_preserves_memory_and_file(self):
        self.create_student()
        before = self.path.read_bytes()
        with patch('storage_manager.catalog.os.replace', side_effect=OSError('failure')):
            with self.assertRaises(StorageError):
                self.catalog.create_table('other', (ColumnSchema('id', DataType.INTEGER),), (), ())
            with self.assertRaises(StorageError):
                self.catalog.register_index(IndexInfo('idx', 'student', 'id'))
        self.assertEqual(self.catalog.list_tables(), ['student'])
        self.assertEqual(self.catalog.list_indexes(), [])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob('.catalog-*.tmp')), [])
        self.catalog.register_index(IndexInfo('idx', 'student', 'id'))
        with patch('storage_manager.catalog.os.replace', side_effect=OSError('failure')):
            with self.assertRaises(StorageError):
                self.catalog.unregister_index('idx')
        self.assertEqual(self.catalog.list_indexes(), Catalog(str(self.path)).list_indexes())

    def test_reload_replaces_state_and_rejects_corruption(self):
        self.create_student()
        self.path.write_text(json.dumps({'version': 1, 'tables': []}))
        self.catalog._load()
        self.assertEqual(self.catalog.list_tables(), [])
        self.create_student()
        for content in ('{broken', '{}', '{"version": 999, "tables": []}'):
            self.path.write_text(content)
            with self.assertRaises(StorageError):
                self.catalog._load()
            self.assertEqual(self.catalog.list_tables(), ['student'])

    def test_facade_reads_persisted_schema(self):
        schema = self.create_student()
        self.catalog.register_index(IndexInfo('idx', 'student', 'id'))
        sm = StorageManager(self.tmp.name)
        self.assertEqual(sm.list_tables(), ['student'])
        self.assertEqual(sm.get_schema('student').columns, schema.columns)
        self.assertEqual(sm.list_indexes('student'), sm.get_schema('student').indexes)
        with self.assertRaises(TableNotFoundError):
            sm.get_schema('missing')

    def test_error_identity(self):
        from storage_manager import errors
        self.assertIs(errors.TableNotFoundError, TableNotFoundError)
        self.assertIs(errors.IndexDefinitionError, IndexDefinitionError)
        self.assertIs(errors.SchemaViolationError, SchemaViolationError)


class TestCatalogRecordSize(unittest.TestCase):
    setUp = TestCatalog.setUp
    create_student = TestCatalog.create_student

    def test_maximum_matches_encoded_record(self):
        from storage_manager.serializer import RecordCodec
        schema = self.create_student()
        # Bitmap + INTEGER + VARCHAR prefix/payload + FLOAT + CHAR.
        self.assertEqual(self.catalog.get_record_size('student'), 69)
        maximum = (1, 'é' * 25, 4.0, 'ABCD')
        self.assertEqual(RecordCodec.record_size(schema, maximum), 69)
        for values in ((1, '', 0.0, ''), (1, None, None, None), maximum):
            self.assertLessEqual(RecordCodec.record_size(schema, values), 69)
        self.assertFalse(hasattr(schema, 'record_size'))
        with self.assertRaises(TableNotFoundError):
            self.catalog.get_record_size('missing')

    def test_bitmap_crosses_byte_boundary(self):
        from storage_manager.serializer import RecordCodec
        schema = self.catalog.create_table('wide', tuple(
            ColumnSchema(f'c{i}', DataType.INTEGER) for i in range(9)), (), ())
        self.assertEqual(self.catalog.get_record_size('wide'), 38)
        self.assertEqual(RecordCodec.record_size(schema, (0,) * 9), 38)
        self.assertEqual(RecordCodec.record_size(schema, (None,) * 9), 2)

    def test_reload_rebuilds_sizes_atomically(self):
        self.create_student()
        restored = Catalog(str(self.path))
        self.assertEqual(restored.get_record_size('student'), 69)
        document = json.loads(self.path.read_text())
        document['tables'][0]['columns'][1]['length'] = 100
        self.path.write_text(json.dumps(document))
        restored._load()
        self.assertEqual(restored.get_record_size('student'), 119)
        document['tables'][0]['columns'][1]['length'] = 65536
        self.path.write_text(json.dumps(document))
        with self.assertRaises(StorageError):
            restored._load()
        self.assertEqual(restored.get_record_size('student'), 119)
        self.path.write_text(json.dumps({'version': 1, 'tables': []}))
        restored._load()
        self.assertEqual(restored._record_size, {})
        self.path.unlink()
        self.catalog._load()
        self.assertEqual(self.catalog._record_size, {})

    def test_failed_create_does_not_leave_size(self):
        with patch('storage_manager.catalog.os.replace', side_effect=OSError('failure')):
            with self.assertRaises(StorageError):
                self.create_student()
        self.assertEqual(self.catalog._record_size, {})

    def test_varchar_prefix_capacity(self):
        from storage_manager.serializer import RecordCodec
        schema = self.catalog.create_table('large', (
            ColumnSchema('text', DataType.VARCHAR, length=65535),), (), ())
        self.assertEqual(self.catalog.get_record_size('large'), 65538)
        self.assertEqual(RecordCodec.record_size(schema, ('a' * 65535,)), 65538)
        with self.assertRaises(SchemaViolationError):
            self.catalog.create_table('invalid', (
                ColumnSchema('text', DataType.VARCHAR, length=65536),), (), ())
        self.assertNotIn('invalid', self.catalog._record_size)
        self.assertNotIn('invalid', self.catalog.list_tables())
