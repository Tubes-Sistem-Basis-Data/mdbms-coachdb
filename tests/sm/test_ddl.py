import os
import tempfile
import unittest
from pathlib import Path

from common.catalog import ColumnSchema, DataType, ForeignKey, IndexInfo, IndexKind, TableSchema
from common.errors import (
    IndexDefinitionError,
    SchemaViolationError,
    SemanticError,
    StorageError,
    TableNotFoundError,
)
from scripts.seed_sample_db import SampleDatabaseSeeder
from storage_manager.facade import StorageManager


def student_schema(name="student", indexes=None):
    return TableSchema(
        name,
        [
            ColumnSchema("id", DataType.INTEGER, nullable=False),
            ColumnSchema("name", DataType.VARCHAR, 20),
        ],
        ["id"],
        [],
        list(indexes or []),
    )


def enroll_schema():
    return TableSchema(
        "enroll",
        [
            ColumnSchema("student_id", DataType.INTEGER, nullable=False),
            ColumnSchema("grade", DataType.FLOAT),
        ],
        ["student_id"],
        [ForeignKey("student_id", "student", "id")],
    )


class FacadeCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name
        self.sm = StorageManager(self.dir)
        self.addCleanup(self.sm.close)

    def reopen(self):
        self.sm.close()
        self.sm = StorageManager(self.dir)
        self.addCleanup(self.sm.close)

    def insert(self, table, values, lsn=1):
        rid = self.sm.allocate_rid(table)
        self.sm.insert(table, rid, values, lsn)
        return rid

    def values(self, table):
        return sorted(row.values for row in self.sm.scan(table))


class TestCreateTable(FacadeCase):
    def test_creates_usable_and_persistent_table(self):
        self.assertIsNone(self.sm.create_table(student_schema()))
        self.assertEqual(self.sm.list_tables(), ["student"])
        self.assertEqual(self.sm.get_schema("student"), student_schema())
        self.assertEqual(self.sm.get_stats("student").n_r, 0)
        self.assertTrue(os.path.exists(os.path.join(self.dir, "student.db")))

        self.insert("student", [1, "Andi"])
        self.reopen()
        self.assertEqual(self.sm.list_tables(), ["student"])
        self.assertEqual(self.values("student"), [[1, "Andi"]])

    def test_duplicate_name_is_semantic_error(self):
        self.sm.create_table(student_schema())
        self.insert("student", [1, "Andi"])
        with self.assertRaises(SemanticError) as ctx:
            self.sm.create_table(student_schema())
        self.assertNotIsInstance(ctx.exception, StorageError)
        self.assertEqual(self.values("student"), [[1, "Andi"]])

    def test_invalid_schema_leaves_no_trace(self):
        bad_fk = TableSchema(
            "enroll",
            [ColumnSchema("student_id", DataType.INTEGER)],
            [],
            [ForeignKey("student_id", "student", "id")],
        )
        cases = [
            bad_fk,
            TableSchema("kosong", []),
            TableSchema("t", [ColumnSchema("id", DataType.INTEGER)], ["tidak_ada"]),
        ]
        for schema in cases:
            with self.subTest(schema=schema.name), self.assertRaises(SchemaViolationError):
                self.sm.create_table(schema)
            self.assertFalse(os.path.exists(os.path.join(self.dir, f"{schema.name}.db")))
        self.assertEqual(self.sm.list_tables(), [])

    def test_rejects_names_unsafe_for_file_paths(self):
        outside = Path(self.dir).parent / "luar.db"
        for name in ["../luar", "a/b", "", "1abc", "nama tabel"]:
            with self.subTest(name=name), self.assertRaises(SchemaViolationError):
                self.sm.create_table(student_schema(name))
        self.assertFalse(outside.exists())
        with self.assertRaises(SchemaViolationError):
            self.sm.create_table("student")

    def test_stale_file_is_not_resurrected(self):
        self.sm.create_table(student_schema())
        self.insert("student", [1, "Andi"])
        self.sm.flush()
        stale = Path(self.dir, "student.db").read_bytes()
        self.sm.drop_table("student")
        # Simulasi crash di tengah DROP: katalog sudah commit, file lama tertinggal.
        Path(self.dir, "student.db").write_bytes(stale)
        self.sm.create_table(student_schema())
        self.assertEqual(self.values("student"), [])

    def test_builds_indexes_from_schema(self):
        index = IndexInfo("idx_name", "student", "name", IndexKind.HASH)
        self.sm.create_table(student_schema(indexes=[index]))
        rid = self.insert("student", [1, "Andi"])
        self.assertEqual(self.sm.index_lookup("student", "name", "Andi"), [rid])
        self.assertEqual(self.sm.list_indexes("student"), [index])

    def test_failed_index_rolls_back_table(self):
        bad = IndexInfo("idx_x", "student", "tidak_ada")
        with self.assertRaises(IndexDefinitionError):
            self.sm.create_table(student_schema(indexes=[bad]))
        self.assertEqual(self.sm.list_tables(), [])
        self.assertFalse(os.path.exists(os.path.join(self.dir, "student.db")))


class TestDropTable(FacadeCase):
    def test_drops_catalog_file_indexes_and_stats(self):
        self.sm.create_table(student_schema())
        self.sm.create_index(IndexInfo("idx_id", "student", "id"))
        self.insert("student", [1, "Andi"])
        self.assertEqual(self.sm.get_stats("student").n_r, 1)

        self.assertIsNone(self.sm.drop_table("student"))
        self.assertEqual(self.sm.list_tables(), [])
        self.assertEqual(self.sm.list_indexes(), [])
        self.assertFalse(os.path.exists(os.path.join(self.dir, "student.db")))
        for call in (
            lambda: self.sm.get_schema("student"),
            lambda: self.sm.get_stats("student"),
            lambda: list(self.sm.scan("student")),
        ):
            with self.assertRaises(TableNotFoundError):
                call()

        self.reopen()
        self.assertEqual(self.sm.list_tables(), [])

    def test_recreated_table_starts_empty_with_fresh_index(self):
        self.sm.create_table(student_schema())
        self.sm.create_index(IndexInfo("idx_id", "student", "id"))
        self.insert("student", [1, "Andi"])
        reserved = self.sm.allocate_rid("student")
        self.sm.drop_table("student")

        self.sm.create_table(student_schema())
        self.sm.create_index(IndexInfo("idx_id", "student", "id"))
        self.assertEqual(self.values("student"), [])
        self.assertEqual(self.sm.get_stats("student").n_r, 0)
        self.assertEqual(self.sm.index_lookup("student", "id", 1), [])
        # RID yang dipesan sebelum DROP tidak boleh dianggap sah di tabel baru.
        with self.assertRaises(StorageError):
            self.sm.insert("student", reserved, [2, "Budi"], 1)

    def test_missing_table(self):
        with self.assertRaises(TableNotFoundError):
            self.sm.drop_table("tidak_ada")

    def test_referenced_table_is_kept_until_referrer_dropped(self):
        self.sm.create_table(student_schema())
        self.sm.create_table(enroll_schema())
        self.insert("student", [1, "Andi"])
        self.sm.flush()

        with self.assertRaises(SemanticError):
            self.sm.drop_table("student")
        self.assertEqual(self.values("student"), [[1, "Andi"]])

        self.sm.drop_table("enroll")
        self.sm.drop_table("student")
        self.assertEqual(self.sm.list_tables(), [])
        self.reopen()
        self.assertEqual(self.sm.list_tables(), [])

    def test_self_reference_does_not_block_drop(self):
        schema = TableSchema(
            "node",
            [
                ColumnSchema("id", DataType.INTEGER, nullable=False),
                ColumnSchema("parent", DataType.INTEGER),
            ],
            ["id"],
            [ForeignKey("parent", "node", "id")],
        )
        self.sm.create_table(schema)
        self.sm.drop_table("node")
        self.assertEqual(self.sm.list_tables(), [])

    def test_snapshot_restore_after_drop(self):
        self.sm.create_table(student_schema())
        self.insert("student", [1, "Andi"])
        snapshot = os.path.join(self.dir, "snap")
        self.sm.snapshot_to(snapshot)
        self.sm.drop_table("student")
        self.sm.restore_from(snapshot)
        self.assertEqual(self.values("student"), [[1, "Andi"]])


class TestStatsAfterMutation(FacadeCase):
    def test_stats_follow_update_delete_and_delete_all(self):
        self.sm.create_table(student_schema())
        rids = [self.insert("student", [i, f"S{i % 2}"]) for i in range(4)]
        stats = self.sm.get_stats("student")
        self.assertEqual((stats.n_r, stats.distinct), (4, {"id": 4, "name": 2}))

        self.sm.update("student", rids[0], [0, "Baru"], 2)
        self.assertEqual(self.sm.get_stats("student").distinct, {"id": 4, "name": 3})

        self.sm.delete("student", rids[1], 3)
        stats = self.sm.get_stats("student")
        self.assertEqual((stats.n_r, stats.distinct), (3, {"id": 3, "name": 3}))

        self.sm.delete_all("student", 4)
        stats = self.sm.get_stats("student")
        self.assertEqual((stats.n_r, stats.b_r), (0, 0))


class TestInstallSampleDatabase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name
        self.seeder = SampleDatabaseSeeder()

    def test_install_into_fresh_data_dir(self):
        counts = self.seeder.install(self.dir)
        self.assertEqual(counts, {"student": 60, "course": 50, "enroll": 200})
        expected = self.seeder.build()
        sm = StorageManager(self.dir)
        try:
            self.assertEqual(sm.list_tables(), ["student", "course", "enroll"])
            for table in expected.list_tables():
                self.assertEqual(sm.get_schema(table), expected.get_schema(table))
                self.assertEqual(
                    sorted(map(tuple, (r.values for r in sm.scan(table)))),
                    sorted(map(tuple, (r.values for r in expected.scan(table)))),
                )
                self.assertEqual(sm.get_stats(table).n_r, counts[table])
        finally:
            sm.close()

    def test_second_install_is_rejected_without_changes(self):
        self.seeder.install(self.dir)
        with self.assertRaises(StorageError):
            self.seeder.install(self.dir)
        sm = StorageManager(self.dir)
        try:
            self.assertEqual(sm.get_stats("student").n_r, 60)
        finally:
            sm.close()

    def test_refuses_data_dir_with_existing_log(self):
        Path(self.dir, "wal.log").write_bytes(b"log lama")
        with self.assertRaises(StorageError):
            self.seeder.install(self.dir)
        self.assertFalse(Path(self.dir, "catalog.json").exists())

    def test_empty_log_is_allowed(self):
        Path(self.dir, "wal.log").write_bytes(b"")
        self.assertEqual(self.seeder.install(self.dir)["enroll"], 200)


if __name__ == "__main__":
    unittest.main()
