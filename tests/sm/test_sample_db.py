import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import Mock, patch

from common import RID, StorageError, SchemaViolationError, TableNotFoundError
from scripts.seed_sample_db import FIXTURE_FILE, SampleDatabase, SampleDatabaseSeeder
from storage_manager.serializer import RecordCodec


class TestSampleDatabase(unittest.TestCase):
    def setUp(self):
        self.seeder = SampleDatabaseSeeder()
        self.db = self.seeder.build()

    def test_counts_types_pk_fk_and_stats(self):
        expected = {'student': 60, 'course': 50, 'enroll': 200}
        for table, count in expected.items():
            schema = self.db.get_schema(table)
            rows = list(self.db.scan(table))
            self.assertEqual(len(rows), count)
            keys = {tuple(r.values[schema.column_index(c)] for c in schema.primary_key) for r in rows}
            self.assertEqual(len(keys), count)
            for row in rows:
                self.assertNotIn(None, row.values)
                RecordCodec.encode(schema, tuple(row.values))
                for fk in schema.foreign_keys:
                    target = self.db.get_schema(fk.ref_table)
                    allowed = {r.values[target.column_index(fk.ref_column)] for r in self.db.scan(fk.ref_table)}
                    self.assertIn(row.values[schema.column_index(fk.column)], allowed)
            stats = self.db.get_stats(table)
            self.assertEqual(stats.n_r, count)
            self.assertEqual(stats.b_r, len({r.rid.page_id for r in rows}))
            self.assertGreater(stats.f_r, 0)
        # Exercises a real cross-page boundary for simulated student records.
        self.assertGreater(self.db.get_stats('student').b_r, 1)

    def test_deterministic_independent_rng_and_calls(self):
        before = random.getstate()
        first = self.seeder._generate_rows('enroll')
        self.seeder._generate_rows('student')
        self.assertEqual(first, self.seeder._generate_rows('enroll'))
        self.assertEqual(before, random.getstate())
        with self.assertRaises(TableNotFoundError):
            self.seeder._generate_rows('unknown')

    def test_defensive_reads_and_rid_validation(self):
        row = next(self.db.scan('student'))
        original = row.values.copy()
        row.values.clear()
        self.assertEqual(self.db.fetch('student', row.rid).values, original)
        self.assertIsNone(self.db.fetch('student', RID('student', 9999, 0)))
        with self.assertRaises(StorageError):
            self.db.fetch('student', RID('course', 0, 0))
        with self.assertRaises(TableNotFoundError):
            self.db.scan('missing')

    def test_fixture_roundtrip_force_and_unrelated_files(self):
        with tempfile.TemporaryDirectory() as directory:
            unrelated = Path(directory) / 'student.db'
            unrelated.write_bytes(b'keep')
            self.seeder.seed(directory, False)
            path = Path(directory) / FIXTURE_FILE
            before = path.read_bytes()
            loaded = self.seeder.load(directory)
            for table in self.db.list_tables():
                self.assertEqual(list(self.db.scan(table)), list(loaded.scan(table)))
                self.assertEqual(self.db.get_stats(table), loaded.get_stats(table))
            with self.assertRaises(StorageError):
                self.seeder.seed(directory, False)
            self.seeder.seed(directory, True)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(unrelated.read_bytes(), b'keep')

    def test_failed_publication_preserves_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            self.seeder.seed(directory, False)
            path = Path(directory) / FIXTURE_FILE
            before = path.read_bytes()
            with patch('scripts.seed_sample_db.os.replace', side_effect=OSError('failure')):
                with self.assertRaises(StorageError):
                    self.seeder.seed(directory, True)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(Path(directory).glob('.sample-*')), [])

    def test_bad_fixture_and_foreign_keys(self):
        rows = {t: [r.values for r in self.db.scan(t)] for t in self.db.list_tables()}
        rows['enroll'][0][0] = 999
        with self.assertRaises(SchemaViolationError):
            SampleDatabase(self.db._catalog, rows)
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / FIXTURE_FILE).write_text('{broken')
            with self.assertRaises(StorageError):
                self.seeder.load(directory)

    def test_storage_import_rejects_stub_and_checks_results(self):
        storage = Mock()
        storage.get_schema.side_effect = self.db.get_schema
        contents = {t: [] for t in self.db.list_tables()}
        storage.scan.side_effect = lambda t: iter(contents[t])
        storage.bulk_load.return_value = 0
        with self.assertRaises(StorageError):
            self.seeder.load_into(storage)
        storage.flush.assert_not_called()
        def bulk_load(table, values):
            from common import Row
            contents[table] = [Row(RID(table, 0, i), v) for i, v in enumerate(values)]
            return len(values)
        storage.bulk_load.side_effect = bulk_load
        self.seeder.load_into(storage)
        storage.flush.assert_called_once()
        with self.assertRaises(StorageError):
            self.seeder.load_into(storage)
