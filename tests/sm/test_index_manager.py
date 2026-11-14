from __future__ import annotations

import tempfile
import unittest

from common.catalog import ColumnSchema, DataType, IndexInfo, IndexKind
from common.errors import IndexDefinitionError, TableNotFoundError
from common.storage import RID, Row
from storage_manager.catalog import Catalog
from storage_manager.facade import StorageManager
from storage_manager.index import IndexManager


def student_schema() -> tuple[ColumnSchema, ...]:
    return (
        ColumnSchema("id", DataType.INTEGER, nullable=False),
        ColumnSchema("code", DataType.CHAR, length=4),
        ColumnSchema("name", DataType.VARCHAR, length=10),
    )


class TestIndexManager(unittest.TestCase):
    def setUp(self):
        self.catalog = Catalog(None)
        self.catalog.create_table("t", student_schema(), ("id",), ())
        self.rows = [
            Row(RID("t", 0, 0), [0, "IF ", "a"]),
            Row(RID("t", 0, 1), [1, "IF ", "b"]),
            Row(RID("t", 0, 2), [2, "GO", "c"]),
            Row(RID("t", 0, 3), [3, "IF", "d"]),
        ]
        self.manager = IndexManager(self.catalog, self._scan)

    def _scan(self, table: str):
        return iter(list(self.rows))

    def rid(self, slot: int) -> RID:
        return RID("t", 0, slot)

    def create_code_index(self, kind: IndexKind = IndexKind.BPLUS_TREE) -> IndexInfo:
        return self.manager.create_index("ix_code", "t", "code", kind)

    # ------------------------------------------------------------------ create

    def test_create_builds_from_existing_rows_and_normalizes_char(self):
        info = self.create_code_index()
        self.assertEqual(info.table, "t")
        self.assertEqual(info.column, "code")
        self.assertEqual(self.manager.lookup("ix_code", "IF"), [self.rid(0), self.rid(1), self.rid(3)])
        self.assertEqual(self.manager.lookup("ix_code", "IF   "), [self.rid(0), self.rid(1), self.rid(3)])
        self.assertEqual(
            self.manager.range("ix_code", "GO", "IF"),
            [self.rid(2), self.rid(0), self.rid(1), self.rid(3)],
        )
        self.assertEqual(self.manager.height("ix_code"), 1)
        self.assertEqual([i.name for i in self.catalog.list_indexes("t")], ["ix_code"])

    def test_lookup_and_range_unknown_index_raise(self):
        with self.assertRaises(IndexDefinitionError):
            self.manager.lookup("missing", 1)
        with self.assertRaises(IndexDefinitionError):
            self.manager.range("missing", 1, 2)
        with self.assertRaises(IndexDefinitionError):
            self.manager.height("missing")

    def test_create_rejects_duplicate_name_column_and_invalid_input(self):
        self.create_code_index()
        cases = [
            ("ix_code", "t", "id"),          # nama sudah dipakai
            ("ix_code_2", "t", "code"),      # kolom sudah punya index
            ("ix_nope", "t", "nope"),        # kolom tidak ada
            ("", "t", "id"),                 # nama kosong
            ("ix_kind", "t", "id", "HASH"),  # kind bukan IndexKind
        ]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(IndexDefinitionError):
                self.manager.create_index(
                    case[0], case[1], case[2], case[3] if len(case) > 3 else IndexKind.BPLUS_TREE
                )

    def test_create_on_missing_table_raises(self):
        with self.assertRaises(TableNotFoundError):
            self.manager.create_index("ix", "nope", "id", IndexKind.BPLUS_TREE)

    def test_hash_index_supports_lookup_but_not_range(self):
        self.create_code_index(IndexKind.HASH)
        self.assertEqual(self.manager.lookup("ix_code", "IF"), [self.rid(0), self.rid(1), self.rid(3)])
        # Hash hanya kesamaan; manager menolak permintaan rentang.
        with self.assertRaises(IndexDefinitionError):
            self.manager.range("ix_code", "GO", "IF")

    # ---------------------------------------------------------------- maintain

    def test_on_insert_and_none_value(self):
        self.create_code_index()
        new_rid = self.rid(4)
        self.manager.on_insert("t", (4, "IF", "e"), new_rid)
        self.assertIn(new_rid, self.manager.lookup("ix_code", "IF"))
        self.manager.on_insert("t", (4, None, "e"), new_rid)
        self.assertNotIn(new_rid, self.manager.lookup("ix_code", "IF"))
        self.assertEqual(self.manager.lookup("ix_code", None), [])

    def test_on_insert_replaces_previous_key_for_same_rid(self):
        self.create_code_index()
        self.manager.on_insert("t", (0, "ZZ", "a"), self.rid(0))
        self.assertEqual(self.manager.lookup("ix_code", "IF"), [self.rid(1), self.rid(3)])
        self.assertEqual(self.manager.lookup("ix_code", "ZZ"), [self.rid(0)])

    def test_on_update_moves_key(self):
        self.create_code_index()
        old = (1, "IF", "b")
        new = (1, "ZZ", "b")
        self.manager.on_update("t", old, new, self.rid(1))
        self.assertEqual(self.manager.lookup("ix_code", "IF"), [self.rid(0), self.rid(3)])
        self.assertEqual(self.manager.lookup("ix_code", "ZZ"), [self.rid(1)])

    def test_on_delete_removes_rid(self):
        self.create_code_index()
        self.manager.on_delete("t", (1, "IF", "b"), self.rid(1))
        self.assertEqual(self.manager.lookup("ix_code", "IF"), [self.rid(0), self.rid(3)])

    def test_rebuild_all_uses_current_table_contents(self):
        self.create_code_index()
        self.rows = [Row(RID("t", 0, 2), [2, "GO", "c"]), Row(RID("t", 0, 4), [4, "IF", "e"])]
        self.manager.rebuild_all()
        self.assertEqual(self.manager.lookup("ix_code", "IF"), [self.rid(4)])
        self.assertEqual(self.manager.lookup("ix_code", "GO"), [self.rid(2)])

    def test_rebuild_is_idempotent(self):
        self.create_code_index()
        expected = self.manager.lookup("ix_code", "IF")
        self.manager.rebuild_all()
        self.manager.rebuild_all()
        self.assertEqual(self.manager.lookup("ix_code", "IF"), expected)


class TestFacadeIndex(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sm = StorageManager(self.tmp.name, 4)
        self.sm._catalog.create_table("student", student_schema(), ("id",), ())
        self.rids = []
        for row in ([0, "IF ", "a"], [1, "IF ", "b"], [2, "GO", "c"], [3, "IF", "d"]):
            rid = self.sm.allocate_rid("student")
            self.sm.insert("student", rid, row, None)
            self.rids.append(rid)
        self.info = IndexInfo("ix_code", "student", "code", IndexKind.BPLUS_TREE)

    def test_create_lookup_range_and_metadata(self):
        self.sm.create_index(self.info)
        self.assertEqual(self.sm.index_lookup("student", "code", "IF"), self.rids[:2] + [self.rids[3]])
        self.assertEqual(
            self.sm.index_range("student", "code", "GO", "IF"),
            [self.rids[2], self.rids[0], self.rids[1], self.rids[3]],
        )
        self.assertEqual(self.sm.list_indexes("student"), [self.info])

    def test_lookup_does_not_scan_table(self):
        from unittest.mock import patch

        self.sm.create_index(self.info)
        with patch.object(
            self.sm._records, "scan", side_effect=AssertionError("index lookup memakai scan")
        ):
            result = self.sm.index_lookup("student", "code", "IF")
        self.assertEqual(result, self.rids[:2] + [self.rids[3]])

    def test_dml_keeps_index_in_sync(self):
        self.sm.create_index(self.info)
        self.sm.update("student", self.rids[0], [0, "ZZ", "a"], None)
        self.assertEqual(self.sm.index_lookup("student", "code", "IF"), [self.rids[1], self.rids[3]])
        self.assertEqual(self.sm.index_lookup("student", "code", "ZZ"), [self.rids[0]])

        self.sm.delete("student", self.rids[1], None)
        self.assertEqual(self.sm.index_lookup("student", "code", "IF"), [self.rids[3]])

    def test_delete_all_clears_index(self):
        self.sm.create_index(self.info)
        self.sm.delete_all("student", None)
        self.assertEqual(self.sm.index_lookup("student", "code", "IF"), [])
        self.assertEqual(self.sm.index_range("student", "code", None, None), [])

    def test_restart_rebuilds_index_from_table_data(self):
        self.sm.create_index(self.info)
        self.sm._buffer.flush_all()
        restarted = StorageManager(self.tmp.name, 4)
        self.assertEqual(
            restarted.index_lookup("student", "code", "IF"),
            self.rids[:2] + [self.rids[3]],
        )
        self.assertEqual(
            restarted.index_range("student", "code", None, "IF"),
            [self.rids[2], self.rids[0], self.rids[1], self.rids[3]],
        )
        self.assertEqual(restarted.list_indexes("student"), [self.info])

    def test_missing_table_column_or_index_raises(self):
        with self.assertRaises(TableNotFoundError):
            self.sm.index_lookup("nope", "code", 1)
        with self.assertRaises(IndexDefinitionError):
            self.sm.index_lookup("student", "nope", 1)
        with self.assertRaises(IndexDefinitionError):
            self.sm.index_lookup("student", "code", "IF")
        with self.assertRaises(IndexDefinitionError):
            self.sm.index_range("student", "code", None, None)

    def test_create_index_duplicate_raises(self):
        self.sm.create_index(self.info)
        with self.assertRaises(IndexDefinitionError):
            self.sm.create_index(self.info)
        with self.assertRaises(IndexDefinitionError):
            self.sm.create_index(IndexInfo("ix_code_2", "student", "code"))

    def test_hash_index_lookup_and_range_rejection(self):
        self.sm.create_index(IndexInfo("ix_id", "student", "id", IndexKind.HASH))
        self.assertEqual(self.sm.index_lookup("student", "id", 2), [self.rids[2]])
        with self.assertRaises(IndexDefinitionError):
            self.sm.index_range("student", "id", 0, 3)


if __name__ == "__main__":
    unittest.main()
