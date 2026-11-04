"""Uji SM-04: mengambil data tabel (scan dan fetch)."""

from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

from common.catalog import ColumnSchema, DataType, TableSchema
from common.errors import StorageError
from common.storage import RID
from storage_manager.buffer import BufferPool
from storage_manager.crud import RecordManager
from storage_manager.errors import TableNotFoundError
from storage_manager.page import PageHeader, SlotStatus

RECORD_SIZE = 4
MAX_SLOTS = 8
STATUS_OFFSET = 4
DATA_OFFSET = STATUS_OFFSET + MAX_SLOTS


class FakeSlottedPage:
    """SlottedPage minimal untuk test: satu page berisi 8 slot x 4 byte."""

    @staticmethod
    def init_page(page_id: int, record_size: int) -> bytearray:
        return bytearray(DATA_OFFSET + MAX_SLOTS * record_size)

    @staticmethod
    def read_header(buf: bytearray) -> PageHeader:
        return PageHeader(0, 0, int.from_bytes(buf[0:4], "little"), RECORD_SIZE, 0)

    @staticmethod
    def read_slot(buf: bytearray, slot: int) -> bytes | None:
        if slot < 0 or slot >= MAX_SLOTS:
            return None
        if buf[STATUS_OFFSET + slot] != SlotStatus.LIVE.value:
            return None
        start = DATA_OFFSET + slot * RECORD_SIZE
        return bytes(buf[start : start + RECORD_SIZE])

    @staticmethod
    def write_slot(buf: bytearray, slot: int, data: bytes) -> None:
        count = int.from_bytes(buf[0:4], "little")
        if slot >= count:
            buf[0:4] = (slot + 1).to_bytes(4, "little")
        buf[STATUS_OFFSET + slot] = SlotStatus.LIVE.value
        start = DATA_OFFSET + slot * RECORD_SIZE
        buf[start : start + RECORD_SIZE] = data

    @staticmethod
    def mark_tombstone(buf: bytearray, slot: int) -> None:
        buf[STATUS_OFFSET + slot] = SlotStatus.TOMBSTONE.value


class FakeCodec:
    @staticmethod
    def decode(schema: TableSchema, data: bytes) -> tuple:
        return (int.from_bytes(data, "little"),)


class FakeDisk:
    def __init__(self, pages: dict[tuple[str, int], bytearray]) -> None:
        self.pages = pages
        self.reads: list[tuple[str, int]] = []

    def read_page(self, table: str, page_id: int) -> bytearray:
        self.reads.append((table, page_id))
        try:
            return bytearray(self.pages[(table, page_id)])
        except KeyError:
            raise StorageError(f"page {table}:{page_id} tidak ada") from None

    def num_pages(self, table: str) -> int:
        ids = [page_id for name, page_id in self.pages if name == table]
        return max(ids) + 1 if ids else 0


class FakeCatalog:
    def __init__(self, schema: TableSchema) -> None:
        self.schema = schema

    def get_table_schema(self, table: str) -> TableSchema:
        if table != self.schema.name:
            raise TableNotFoundError(f"tabel {table} tidak ada")
        return self.schema


SCHEMA = TableSchema(name="student", columns=[ColumnSchema("id", DataType.INTEGER)])


def make_page(rows: list[int | None]) -> bytearray:
    page = FakeSlottedPage.init_page(0, RECORD_SIZE)
    for slot, value in enumerate(rows):
        if value is None:
            FakeSlottedPage.write_slot(page, slot, b"\x00" * RECORD_SIZE)
            FakeSlottedPage.mark_tombstone(page, slot)
        else:
            FakeSlottedPage.write_slot(page, slot, value.to_bytes(RECORD_SIZE, "little"))
    return page


class BaseReadTest(unittest.TestCase):
    def make_records(self, pages: dict[tuple[str, int], bytearray]) -> RecordManager:
        self.disk = FakeDisk(pages)
        self.buffer = BufferPool(self.disk, 4)
        self.records = RecordManager(
            threading.RLock(), self.buffer, FakeCatalog(SCHEMA), None, None
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
        page = FakeSlottedPage.init_page(0, RECORD_SIZE)
        FakeSlottedPage.write_slot(page, 0, (10).to_bytes(RECORD_SIZE, "little"))
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

    def test_pin_count_kembali_nol(self) -> None:
        self.make_records({("student", 0): make_page([10])})
        self.fetch(RID("student", 0, 0))
        self.assertEqual(self.buffer._frames[("student", 0)].pin_count, 0)


if __name__ == "__main__":
    unittest.main()
