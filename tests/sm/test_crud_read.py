"""Uji SM-04: mengambil data tabel (scan dan fetch)."""

from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

from common.errors import StorageError
from common.storage import RID
from storage_manager.buffer import BufferPool
from storage_manager.crud import RecordManager
from storage_manager.errors import TableNotFoundError
from tests.sm.fakes import (
    FakeCatalog,
    FakeCodec,
    FakeDisk,
    FakeSlottedPage,
    make_page,
)


class BaseReadTest(unittest.TestCase):
    def make_records(self, pages: dict[tuple[str, int], bytearray]) -> RecordManager:
        self.disk = FakeDisk(pages)
        self.buffer = BufferPool(self.disk, 4)
        self.records = RecordManager(
            threading.RLock(), self.buffer, FakeCatalog(), None, None
        )
        return self.records

    def scan(self, table: str = "student") -> list:
        with patch("storage_manager.crud.SlottedPage", FakeSlottedPage), patch(
            "storage_manager.crud.RecordCodec", FakeCodec
        ):
            return list(self.records.scan(table))

    def fetch(self, rid: RID, table: str = "student"):
        with patch("storage_manager.crud.SlottedPage", FakeSlottedPage), patch(
            "storage_manager.crud.RecordCodec", FakeCodec
        ):
            return self.records.fetch(table, rid)


class TestScan(BaseReadTest):
    def test_tabel_kosong_tidak_menghasilkan_baris(self) -> None:
        self.make_records({})
        self.assertEqual(self.scan(), [])

    def test_urutan_hasil_page_dan_slot(self) -> None:
        self.make_records(
            {
                ("student", 0): make_page([10, 11]),
                ("student", 1): make_page([20]),
            }
        )
        rows = self.scan()
        self.assertEqual(
            [row.rid for row in rows],
            [RID("student", 0, 0), RID("student", 0, 1), RID("student", 1, 0)],
        )
        self.assertEqual([row.values for row in rows], [[10], [11], [20]])

    def test_tombstone_tidak_muncul(self) -> None:
        self.make_records({("student", 0): make_page([10, None, 12])})
        self.assertEqual([row.rid.slot for row in self.scan()], [0, 2])

    def test_tabel_tidak_ada_raise_table_not_found(self) -> None:
        self.make_records({})
        with self.assertRaises(TableNotFoundError):
            self.scan("dosen")

    def test_pin_count_kembali_nol(self) -> None:
        self.make_records({("student", 0): make_page([10])})
        self.scan()
        self.assertEqual(self.buffer._frames[("student", 0)].pin_count, 0)


class TestFetch(BaseReadTest):
    def test_rid_valid_mengembalikan_baris(self) -> None:
        self.make_records({("student", 0): make_page([10, 11])})
        row = self.fetch(RID("student", 0, 1))
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row.rid, RID("student", 0, 1))
        self.assertEqual(row.values, [11])

    def test_slot_kosong_mengembalikan_none(self) -> None:
        page = FakeSlottedPage.init_page(0, 4)
        FakeSlottedPage.write_slot(page, 0, (10).to_bytes(4, "little"))
        self.make_records({("student", 0): page})
        self.assertIsNone(self.fetch(RID("student", 0, 5)))

    def test_tombstone_mengembalikan_none(self) -> None:
        self.make_records({("student", 0): make_page([10, None])})
        self.assertIsNone(self.fetch(RID("student", 0, 1)))

    def test_page_tidak_ada_mengembalikan_none(self) -> None:
        self.make_records({})
        self.assertIsNone(self.fetch(RID("student", 7, 0)))

    def test_tabel_tidak_ada_raise_table_not_found(self) -> None:
        self.make_records({})
        with self.assertRaises(TableNotFoundError):
            self.fetch(RID("dosen", 0, 0), table="dosen")

    def test_rid_tabel_tidak_sama_raise_storage_error(self) -> None:
        self.make_records({("student", 0): make_page([10])})
        with self.assertRaises(StorageError):
            self.fetch(RID("dosen", 0, 0), table="student")

    def test_pin_count_kembali_nol(self) -> None:
        self.make_records({("student", 0): make_page([10])})
        self.fetch(RID("student", 0, 0))
        self.assertEqual(self.buffer._frames[("student", 0)].pin_count, 0)


if __name__ == "__main__":
    unittest.main()
