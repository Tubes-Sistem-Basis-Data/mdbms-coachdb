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

    def log(self, rid: RID) -> int:
        return 1


class TestInsert(BaseInsertTest):
    def test_rid_berurutan(self) -> None:
        rids = [self.records.insert("student", (i,), self.log) for i in range(3)]
        self.assertEqual(
            rids,
            [RID("student", 0, 0), RID("student", 0, 1), RID("student", 0, 2)],
        )

    def test_page_baru_saat_page_penuh(self) -> None:
        rids = [self.records.insert("student", (i,), self.log) for i in range(9)]
        self.assertEqual(rids[7], RID("student", 0, 7))
        self.assertEqual(rids[8], RID("student", 1, 0))

    def test_log_fn_dipanggil_sekali_dengan_rid_benar(self) -> None:
        calls: list[RID] = []

        def log(rid: RID) -> int:
            calls.append(rid)
            return 1

        rid = self.records.insert("student", (5,), log)
        self.assertEqual(calls, [rid])

    def test_log_fn_gagal_tidak_meninggalkan_record(self) -> None:
        def gagal(rid: RID) -> int:
            raise StorageError("log gagal")

        with self.assertRaises(StorageError):
            self.records.insert("student", (5,), gagal)
        self.assertEqual(list(self.records.scan("student")), [])
        for frame in self.buffer._frames.values():
            self.assertEqual(frame.pin_count, 0)

    def test_nilai_invalid_ditolak_tanpa_memanggil_log_fn(self) -> None:
        calls: list[RID] = []

        def log(rid: RID) -> int:
            calls.append(rid)
            return 1

        with patch.object(
            FakeCodec, "validate", side_effect=SchemaViolationError("nilai salah")
        ):
            with self.assertRaises(SchemaViolationError):
                self.records.insert("student", (1,), log)
        self.assertEqual(calls, [])

    def test_tombstone_tidak_dipakai_ulang(self) -> None:
        self.disk.pages[("student", 0)] = make_page([None])
        rid = self.records.insert("student", (5,), self.log)
        self.assertEqual(rid, RID("student", 0, 1))


class TestInsertPrimaryKey(BaseInsertTest):
    schema = PK_SCHEMA

    def test_primary_key_ganda_raise_schema_violation(self) -> None:
        self.records.insert("student", (1,), self.log)
        with self.assertRaises(SchemaViolationError):
            self.records.insert("student", (1,), self.log)


class TestRidAllocation(BaseInsertTest):
    def test_allocate_rid_unik(self) -> None:
        first = self.records.allocate_rid("student")
        second = self.records.allocate_rid("student")
        self.assertNotEqual(first, second)

    def test_release_rid_membatalkan_alokasi(self) -> None:
        first = self.records.allocate_rid("student")
        self.records.release_rid(first)
        self.assertEqual(self.records.allocate_rid("student"), first)


class TestInsertAt(BaseInsertTest):
    def test_insert_at_idempoten(self) -> None:
        rid = RID("student", 0, 0)
        self.disk.pages[("student", 0)] = make_page([])
        self.records.insert_at("student", rid, (7,), 1)
        self.records.insert_at("student", rid, (7,), 2)
        self.assertEqual(len(self.index.inserts), 1)
        self.assertEqual(self.stats.inserts, 1)
        self.assertEqual([row.values for row in self.records.scan("student")], [[7]])

    def test_insert_at_menghidupkan_tombstone(self) -> None:
        rid = RID("student", 0, 0)
        self.disk.pages[("student", 0)] = make_page([None])
        self.records.insert_at("student", rid, (9,), 1)
        self.assertEqual([row.values for row in self.records.scan("student")], [[9]])


if __name__ == "__main__":
    unittest.main()
