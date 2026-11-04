"""Uji SM-06: mengubah data (update)."""

from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

from common.catalog import ColumnSchema, DataType, IndexInfo, TableSchema
from common.storage import RID
from storage_manager.buffer import BufferPool
from storage_manager.crud import RecordManager
from storage_manager.errors import SchemaViolationError
from tests.sm.fakes import (
    FakeCatalog,
    FakeCodec,
    FakeDisk,
    FakeIndexManager,
    FakeSlottedPage,
    FakeStats,
    make_page,
)

RECORD_SIZE = 8

SCHEMA = TableSchema(
    name="student",
    columns=[
        ColumnSchema("id", DataType.INTEGER),
        ColumnSchema("name", DataType.INTEGER),
    ],
    primary_key=["id"],
)

INDEX_ID = IndexInfo(name="idx_id", table="student", column="id")


class BaseUpdateTest(unittest.TestCase):
    indexes: list = []

    def setUp(self) -> None:
        self.disk = FakeDisk()
        self.buffer = BufferPool(self.disk, 16)
        self.index = FakeIndexManager()
        self.stats = FakeStats()
        self.catalog = FakeCatalog(SCHEMA, self.indexes)
        self.records = RecordManager(
            threading.RLock(), self.buffer, self.catalog, self.index, self.stats
        )
        self._patches = [
            patch("storage_manager.crud.SlottedPage", FakeSlottedPage),
            patch("storage_manager.crud.RecordCodec", FakeCodec),
            patch("storage_manager.buffer._slotted_page", lambda: FakeSlottedPage),
        ]
        for patcher in self._patches:
            patcher.start()

    def tearDown(self) -> None:
        for patcher in reversed(self._patches):
            patcher.stop()

    def put(self, rows: list) -> None:
        self.disk.pages[("student", 0)] = make_page(
            rows, record_size=RECORD_SIZE, schema=SCHEMA
        )

    def fetch(self, rid: RID):
        return self.records.fetch("student", rid)


class TestUpdate(BaseUpdateTest):
    def test_rid_tidak_berubah(self) -> None:
        self.put([(1, 10)])
        rid = RID("student", 0, 0)
        self.records.update("student", rid, (1, 20), 5)
        row = self.fetch(rid)
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row.rid, rid)
        self.assertEqual(row.values, [1, 20])

    def test_update_idempoten(self) -> None:
        self.put([(1, 10)])
        rid = RID("student", 0, 0)
        self.records.update("student", rid, (1, 20), 5)
        self.records.update("student", rid, (1, 20), 6)
        row = self.fetch(rid)
        assert row is not None
        self.assertEqual(row.values, [1, 20])
        self.assertEqual(self.stats.invalidations, 1)
        self.assertEqual(self.buffer._frames[("student", 0)].page_lsn, 5)

    def test_statistik_di_invalidate(self) -> None:
        self.put([(1, 10)])
        self.records.update("student", RID("student", 0, 0), (1, 20), 1)
        self.assertEqual(self.stats.invalidations, 1)

    def test_slot_bukan_live(self) -> None:
        self.put([None])
        rid = RID("student", 0, 0)
        self.records.update("student", rid, (1, 20), 1)
        self.assertIsNone(self.fetch(rid))
        self.assertEqual(self.stats.invalidations, 0)
        self.assertEqual(self.index.updates, [])

    def test_page_tidak_ada(self) -> None:
        self.records.update("student", RID("student", 7, 0), (1, 20), 1)
        self.assertEqual(self.stats.invalidations, 0)

    def test_pin_count_kembali_nol(self) -> None:
        self.put([(1, 10)])
        self.records.update("student", RID("student", 0, 0), (1, 20), 1)
        self.assertEqual(self.buffer._frames[("student", 0)].pin_count, 0)


class TestUpdateIndex(BaseUpdateTest):
    indexes = [INDEX_ID]

    def test_index_diperbarui_saat_kolom_terindex_berubah(self) -> None:
        self.put([(1, 10)])
        rid = RID("student", 0, 0)
        self.records.update("student", rid, (2, 10), 1)
        self.assertEqual(self.index.updates, [("student", (1, 10), (2, 10), rid)])

    def test_index_tidak_disentuh_saat_kolom_biasa_berubah(self) -> None:
        self.put([(1, 10)])
        rid = RID("student", 0, 0)
        self.records.update("student", rid, (1, 99), 1)
        self.assertEqual(self.index.updates, [])

    def test_index_tidak_disentuh_saat_update_idempoten(self) -> None:
        self.put([(1, 10)])
        rid = RID("student", 0, 0)
        self.records.update("student", rid, (2, 10), 1)
        self.records.update("student", rid, (2, 10), 2)
        self.assertEqual(len(self.index.updates), 1)


class TestUpdatePrimaryKey(BaseUpdateTest):
    def test_primary_key_ganda_mengecualikan_record_sendiri(self) -> None:
        self.put([(1, 10), (2, 20)])
        rid = RID("student", 0, 0)
        self.records.update("student", rid, (1, 99), 1)
        row = self.fetch(rid)
        assert row is not None
        self.assertEqual(row.values, [1, 99])

    def test_primary_key_bentrok_record_lain_ditolak(self) -> None:
        self.put([(1, 10), (2, 20)])
        rid = RID("student", 0, 0)
        with self.assertRaises(SchemaViolationError):
            self.records.update("student", rid, (2, 99), 1)
        row = self.fetch(rid)
        assert row is not None
        self.assertEqual(row.values, [1, 10])


if __name__ == "__main__":
    unittest.main()
