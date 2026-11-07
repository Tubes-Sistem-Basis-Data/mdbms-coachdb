"""Uji SM-07: menghapus data (delete dan delete_all)."""

from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

from common.errors import StorageError
from common.storage import RID
from storage_manager.buffer import BufferPool
from storage_manager.crud import RecordManager
from storage_manager.page import SlotStatus
from tests.sm.fakes import (
    FakeCatalog,
    FakeCodec,
    FakeDisk,
    FakeIndexManager,
    FakeSlottedPage,
    FakeStats,
    make_page,
)


class BaseDeleteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.disk = FakeDisk()
        self.buffer = BufferPool(self.disk, 16)
        self.index = FakeIndexManager()
        self.stats = FakeStats()
        self.records = RecordManager(
            threading.RLock(),
            self.buffer,
            FakeCatalog(),
            self.index,
            self.stats,
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
        self.disk.pages[("student", 0)] = make_page(rows)

    def insert(self, values: tuple) -> RID:
        rid = self.records.allocate_rid("student")
        self.records.insert("student", rid, values, 1)
        return rid


class TestDelete(BaseDeleteTest):
    def test_fetch_setelah_delete_mengembalikan_none(self) -> None:
        self.put([10, 20])
        rid = RID("student", 0, 0)
        self.records.delete("student", rid, 3)
        self.assertIsNone(self.records.fetch("student", rid))
        self.assertEqual([row.values for row in self.records.scan("student")], [[20]])

    def test_delete_dua_kali_aman_lsn_none(self) -> None:
        self.put([10])
        rid = RID("student", 0, 0)
        self.records.delete("student", rid, None)
        self.records.delete("student", rid, None)
        self.assertEqual(len(self.index.deletes), 1)
        self.assertEqual(self.stats.deletes, 1)
        self.assertEqual(self.buffer._frames[("student", 0)].page_lsn, 0)

    def test_delete_dua_kali_lsn_raise(self) -> None:
        self.put([10])
        rid = RID("student", 0, 0)
        self.records.delete("student", rid, 3)
        with self.assertRaises(StorageError):
            self.records.delete("student", rid, 4)
        self.assertEqual(self.buffer._frames[("student", 0)].page_lsn, 3)

    def test_slot_kosong_raise_untuk_lsn_terisi(self) -> None:
        self.put([10])
        with self.assertRaises(StorageError):
            self.records.delete("student", RID("student", 0, 5), 3)
        self.assertEqual(self.index.deletes, [])
        self.assertEqual(self.stats.deletes, 0)

    def test_slot_empty_jadi_tombstone_untuk_lsn_none(self) -> None:
        self.put([10])
        rid = RID("student", 0, 5)
        self.records.delete("student", rid, None)
        page = self.buffer._frames[("student", 0)].data
        self.assertEqual(
            FakeSlottedPage.slot_status(page, 5), SlotStatus.TOMBSTONE
        )
        self.assertEqual(self.index.deletes, [])
        self.assertEqual(self.stats.deletes, 0)

    def test_page_tidak_ada_aman_untuk_lsn_none(self) -> None:
        self.records.delete("student", RID("student", 7, 0), None)
        self.assertEqual(self.stats.deletes, 0)

    def test_page_tidak_ada_raise_untuk_lsn_terisi(self) -> None:
        with self.assertRaises(StorageError):
            self.records.delete("student", RID("student", 7, 0), 3)

    def test_rid_tidak_dipakai_ulang_insert(self) -> None:
        self.put([10])
        self.records.delete("student", RID("student", 0, 0), 3)
        rid = self.insert((20,))
        self.assertNotEqual(rid, RID("student", 0, 0))
        self.assertEqual(rid, RID("student", 0, 1))

    def test_index_dan_statistik_diperbarui(self) -> None:
        self.put([10])
        rid = RID("student", 0, 0)
        self.records.delete("student", rid, 3)
        self.assertEqual(self.index.deletes, [("student", (10,), rid)])
        self.assertEqual(self.stats.deletes, 1)
        self.assertEqual(self.stats.invalidations, 1)

    def test_pin_count_kembali_nol(self) -> None:
        self.put([10])
        self.records.delete("student", RID("student", 0, 0), 3)
        self.assertEqual(self.buffer._frames[("student", 0)].pin_count, 0)

    def test_rid_tabel_tidak_sama_raise_storage_error(self) -> None:
        self.put([10])
        with self.assertRaises(StorageError):
            self.records.delete("student", RID("dosen", 0, 0), 3)


class TestDeleteAll(BaseDeleteTest):
    def test_jumlah_record_terhapus_benar(self) -> None:
        self.put([10, 20, None, 30])
        self.records.delete_all("student", 5)
        self.assertEqual(list(self.records.scan("student")), [])
        self.assertEqual(len(self.index.deletes), 3)

    def test_scan_kosong_setelah_delete_all(self) -> None:
        self.put([10, 20])
        self.records.delete_all("student", 5)
        self.assertEqual(list(self.records.scan("student")), [])
        self.assertIsNone(self.records.fetch("student", RID("student", 0, 0)))
        self.assertEqual(self.buffer._frames[("student", 0)].page_lsn, 5)

    def test_index_tabel_kosong(self) -> None:
        self.put([10, 20])
        self.records.delete_all("student", 5)
        self.assertEqual(
            self.index.deletes,
            [
                ("student", (10,), RID("student", 0, 0)),
                ("student", (20,), RID("student", 0, 1)),
            ],
        )

    def test_delete_all_idempoten(self) -> None:
        self.put([10, 20])
        self.records.delete_all("student", 5)
        self.records.delete_all("student", None)
        self.assertEqual(len(self.index.deletes), 2)
        self.assertEqual(self.stats.invalidations, 1)

    def test_delete_all_lsn_none_tidak_mengubah_page_lsn(self) -> None:
        self.put([10, 20])
        self.records.delete_all("student", None)
        self.assertEqual(list(self.records.scan("student")), [])
        self.assertEqual(self.buffer._frames[("student", 0)].page_lsn, 0)

    def test_tabel_kosong_tidak_error(self) -> None:
        self.records.delete_all("student", 5)
        self.assertEqual(self.stats.invalidations, 0)


if __name__ == "__main__":
    unittest.main()
