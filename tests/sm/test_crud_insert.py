"""Uji SM-05: menambah data (insert, allocate_rid, release_rid)."""

from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

from common.catalog import ColumnSchema, DataType, TableSchema
from common.errors import StorageError
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

PK_SCHEMA = TableSchema(
    name="student",
    columns=[ColumnSchema("id", DataType.INTEGER)],
    primary_key=["id"],
)


class BaseInsertTest(unittest.TestCase):
    schema = None

    def setUp(self) -> None:
        self.disk = FakeDisk()
        self.buffer = BufferPool(self.disk, 16)
        self.index = FakeIndexManager()
        self.stats = FakeStats()
        catalog = FakeCatalog(self.schema) if self.schema else FakeCatalog()
        self.records = RecordManager(
            threading.RLock(), self.buffer, catalog, self.index, self.stats
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

    def insert(self, values: tuple, lsn: int = 1) -> RID:
        rid = self.records.allocate_rid("student")
        self.records.insert("student", rid, values, lsn)
        return rid


class TestInsert(BaseInsertTest):
    def test_rid_berurutan(self) -> None:
        rids = [self.insert((i,)) for i in range(3)]
        self.assertEqual(
            rids,
            [RID("student", 0, 0), RID("student", 0, 1), RID("student", 0, 2)],
        )

    def test_page_baru_saat_page_penuh(self) -> None:
        rids = [self.insert((i,)) for i in range(9)]
        self.assertEqual(rids[7], RID("student", 0, 7))
        self.assertEqual(rids[8], RID("student", 1, 0))

    def test_insert_mengatur_page_lsn(self) -> None:
        rid = self.insert((5,), lsn=42)
        self.assertEqual(self.buffer._frames[("student", 0)].page_lsn, 42)
        self.assertEqual(self.buffer._frames[("student", 0)].pin_count, 0)
        row = self.records.fetch("student", rid)
        assert row is not None
        self.assertEqual(row.values, [5])

    def test_insert_tanpa_alokasi_raise_storage_error(self) -> None:
        with self.assertRaises(StorageError):
            self.records.insert("student", RID("student", 0, 0), (5,), 1)

    def test_insert_setelah_release_raise_storage_error(self) -> None:
        rid = self.records.allocate_rid("student")
        self.records.release_rid(rid)
        with self.assertRaises(StorageError):
            self.records.insert("student", rid, (5,), 1)

    def test_nilai_invalid_ditolak_sebelum_menulis(self) -> None:
        rid = self.records.allocate_rid("student")
        with patch.object(
            FakeCodec, "validate", side_effect=SchemaViolationError("nilai salah")
        ):
            with self.assertRaises(SchemaViolationError):
                self.records.insert("student", rid, (1,), 1)
        self.assertEqual(self.records._reserved, {rid})
        self.assertEqual(list(self.records.scan("student")), [])

    def test_tombstone_tidak_dipakai_ulang(self) -> None:
        self.disk.pages[("student", 0)] = make_page([None])
        rid = self.insert((5,))
        self.assertEqual(rid, RID("student", 0, 1))

    def test_rid_tabel_tidak_sama_raise_storage_error(self) -> None:
        rid = self.records.allocate_rid("student")
        with self.assertRaises(StorageError):
            self.records.insert("student", RID("dosen", rid.page_id, rid.slot), (5,), 1)


class TestInsertPrimaryKey(BaseInsertTest):
    schema = PK_SCHEMA

    def test_primary_key_ganda_raise_schema_violation(self) -> None:
        self.insert((1,))
        with self.assertRaises(SchemaViolationError):
            self.insert((1,))

    def test_pk_dilewati_saat_lsn_none(self) -> None:
        self.insert((1,))
        rid = RID("student", 0, 5)
        self.records.insert("student", rid, (1,), None)
        self.assertEqual([row.values for row in self.records.scan("student")], [[1], [1]])


class TestRidAllocation(BaseInsertTest):
    def test_allocate_rid_unik(self) -> None:
        first = self.records.allocate_rid("student")
        second = self.records.allocate_rid("student")
        self.assertNotEqual(first, second)

    def test_release_rid_membatalkan_alokasi(self) -> None:
        first = self.records.allocate_rid("student")
        self.records.release_rid(first)
        self.assertEqual(self.records.allocate_rid("student"), first)


class TestInsertUpsert(BaseInsertTest):
    def test_insert_none_idempoten(self) -> None:
        rid = RID("student", 0, 0)
        self.disk.pages[("student", 0)] = make_page([])
        self.records.insert("student", rid, (7,), None)
        self.records.insert("student", rid, (7,), None)
        self.assertEqual(len(self.index.inserts), 1)
        self.assertEqual(self.stats.inserts, 1)
        self.assertEqual([row.values for row in self.records.scan("student")], [[7]])

    def test_insert_none_menghidupkan_tombstone(self) -> None:
        rid = RID("student", 0, 0)
        self.disk.pages[("student", 0)] = make_page([None])
        self.records.insert("student", rid, (9,), None)
        self.assertEqual([row.values for row in self.records.scan("student")], [[9]])

    def test_insert_none_tidak_mengubah_page_lsn(self) -> None:
        rid = RID("student", 0, 0)
        self.disk.pages[("student", 0)] = make_page([None])
        self.records.insert("student", rid, (9,), None)
        self.assertEqual(self.buffer._frames[("student", 0)].page_lsn, 0)


if __name__ == "__main__":
    unittest.main()
