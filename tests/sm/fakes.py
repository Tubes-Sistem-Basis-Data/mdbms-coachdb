"""Test double Storage Manager untuk pengujian RecordManager tanpa SM-01/SM-03."""

from __future__ import annotations

from common.catalog import ColumnSchema, DataType, TableSchema
from common.errors import StorageError
from storage_manager.errors import TableNotFoundError
from storage_manager.page import PageHeader, SlotStatus

RECORD_SIZE = 4
MAX_SLOTS = 8
SLOT_COUNT_OFFSET = 0
LSN_OFFSET = 4
STATUS_OFFSET = 12
DATA_OFFSET = STATUS_OFFSET + MAX_SLOTS


class FakeSlottedPage:
    """SlottedPage minimal: satu page berisi 8 slot x 4 byte plus header LSN."""

    @staticmethod
    def init_page(page_id: int, record_size: int) -> bytearray:
        return bytearray(DATA_OFFSET + MAX_SLOTS * record_size)

    @staticmethod
    def read_header(buf: bytearray) -> PageHeader:
        slot_count = int.from_bytes(
            buf[SLOT_COUNT_OFFSET : SLOT_COUNT_OFFSET + 4], "little"
        )
        page_lsn = int.from_bytes(buf[LSN_OFFSET : LSN_OFFSET + 8], "little")
        return PageHeader(0, page_lsn, slot_count, RECORD_SIZE, 0)

    @staticmethod
    def set_page_lsn(buf: bytearray, lsn: int) -> None:
        buf[LSN_OFFSET : LSN_OFFSET + 8] = int(lsn).to_bytes(8, "little")

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
        count = int.from_bytes(
            buf[SLOT_COUNT_OFFSET : SLOT_COUNT_OFFSET + 4], "little"
        )
        if slot >= count:
            buf[SLOT_COUNT_OFFSET : SLOT_COUNT_OFFSET + 4] = (slot + 1).to_bytes(
                4, "little"
            )
        buf[STATUS_OFFSET + slot] = SlotStatus.LIVE.value
        start = DATA_OFFSET + slot * RECORD_SIZE
        buf[start : start + RECORD_SIZE] = data

    @staticmethod
    def mark_tombstone(buf: bytearray, slot: int) -> None:
        buf[STATUS_OFFSET + slot] = SlotStatus.TOMBSTONE.value

    @staticmethod
    def first_never_used_slot(buf: bytearray) -> int | None:
        for slot in range(MAX_SLOTS):
            if buf[STATUS_OFFSET + slot] == SlotStatus.EMPTY.value:
                return slot
        return None

    @staticmethod
    def capacity(record_size: int) -> int:
        return MAX_SLOTS

    @staticmethod
    def seal(buf: bytearray) -> None:
        return None


class FakeCodec:
    """RecordCodec minimal untuk satu kolom INTEGER."""

    @staticmethod
    def record_size(columns) -> int:
        return RECORD_SIZE

    @staticmethod
    def validate(schema: TableSchema, values: tuple) -> tuple:
        return tuple(values)

    @staticmethod
    def encode(schema: TableSchema, values: tuple) -> bytes:
        return int(values[0]).to_bytes(RECORD_SIZE, "little")

    @staticmethod
    def decode(schema: TableSchema, data: bytes) -> tuple:
        return (int.from_bytes(data, "little"),)


class FakeDisk:
    def __init__(self, pages: dict[tuple[str, int], bytearray] | None = None) -> None:
        self.pages = pages or {}
        self.reads: list[tuple[str, int]] = []
        self.writes: list[tuple[str, int]] = []
        self.allocations: list[tuple[str, int]] = []

    def read_page(self, table: str, page_id: int) -> bytearray:
        self.reads.append((table, page_id))
        try:
            return bytearray(self.pages[(table, page_id)])
        except KeyError:
            raise StorageError(f"page {table}:{page_id} tidak ada") from None

    def write_page(self, table: str, page_id: int, data: bytes) -> None:
        self.pages[(table, page_id)] = bytearray(data)
        self.writes.append((table, page_id))

    def allocate_page(self, table: str, data: bytes) -> int:
        page_id = self.num_pages(table)
        self.pages[(table, page_id)] = bytearray(data)
        self.allocations.append((table, page_id))
        return page_id

    def num_pages(self, table: str) -> int:
        ids = [page_id for name, page_id in self.pages if name == table]
        return max(ids) + 1 if ids else 0

    def sync(self, table: str | None = None) -> None:
        return None


class FakeCatalog:
    def __init__(self, schema: TableSchema | None = None) -> None:
        self.schema = schema or SCHEMA

    def get_table_schema(self, table: str) -> TableSchema:
        if table != self.schema.name:
            raise TableNotFoundError(f"tabel {table} tidak ada")
        return self.schema

    def list_indexes(self, table: str | None = None) -> list:
        return []


class FakeIndexManager:
    def __init__(self) -> None:
        self.inserts: list[tuple[str, tuple, object]] = []
        self.deletes: list[tuple[str, tuple, object]] = []
        self.updates: list[tuple[str, tuple, tuple, object]] = []

    def on_insert(self, table: str, values: tuple, rid) -> None:
        self.inserts.append((table, tuple(values), rid))

    def on_delete(self, table: str, values: tuple, rid) -> None:
        self.deletes.append((table, tuple(values), rid))

    def on_update(self, table: str, old: tuple, new: tuple, rid) -> None:
        self.updates.append((table, tuple(old), tuple(new), rid))


class FakeStats:
    def __init__(self) -> None:
        self.inserts = 0
        self.deletes = 0
        self.invalidations = 0

    def on_insert(self, table: str) -> None:
        self.inserts += 1

    def on_delete(self, table: str) -> None:
        self.deletes += 1

    def invalidate(self, table: str) -> None:
        self.invalidations += 1


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
