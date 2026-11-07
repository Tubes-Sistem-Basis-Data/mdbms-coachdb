"""Record Manager (SM-04..SM-07): CRUD di atas SlottedPage."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Callable, Iterator, Optional

from common.errors import StorageError
from common.storage import RID, Row
from storage_manager.buffer import Frame
from storage_manager.errors import SchemaViolationError
from storage_manager.page import SlotStatus, SlottedPage
from storage_manager.serializer import RecordCodec
from storage_manager.types import LSN, Value

if TYPE_CHECKING:
    from storage_manager.buffer import BufferPool
    from storage_manager.catalog import Catalog
    from storage_manager.index import IndexManager
    from storage_manager.stats import StatisticsManager


class RecordManager:
    """Kelola record: validasi, CRUD idempoten, dan alokasi RID (T3, D9)."""

    def __init__(
        self,
        lock: threading.RLock,
        buffer: BufferPool,
        catalog: Catalog,
        index_manager: IndexManager,
        stats: StatisticsManager,
    ) -> None:
        self._lock = lock
        self._last_page: dict[str, int] = {}
        self._reserved: set[RID] = set()
        self._buffer = buffer
        self._catalog = catalog
        self._index_manager = index_manager
        self._stats = stats

    def validate(
        self,
        table: str,
        values: tuple[Value, ...],
        rid: RID | None = None,
    ) -> tuple[Value, ...]:
        schema = self._catalog.get_table_schema(table)
        normalized = RecordCodec.validate(schema, values)
        self._check_primary_key(table, normalized, rid)
        return normalized

    def scan(self, table: str) -> Iterator[Row]:
        schema = self._catalog.get_table_schema(table)
        page_count = self._buffer._disk.num_pages(table)
        for page_id in range(page_count):
            rows: list[Row] = []
            with self._lock:
                frame = self._buffer.get_page(table, page_id)
                try:
                    header = SlottedPage.read_header(frame.data)
                    for slot in range(header.slot_count):
                        data = SlottedPage.read_slot(frame.data, slot)
                        if data is None:
                            continue
                        values = list(RecordCodec.decode(schema, data))
                        rows.append(Row(RID(table, page_id, slot), values))
                finally:
                    self._buffer.unpin(frame, False)
            for row in rows:
                yield row

    def fetch(self, table: str, rid: RID) -> Optional[Row]:
        self._check_rid_table(table, rid)
        schema = self._catalog.get_table_schema(table)
        with self._lock:
            try:
                frame = self._buffer.get_page(table, rid.page_id)
            except (StorageError, KeyError):
                # Page tidak ada, misalnya file tabel belum pernah dibuat.
                return None
            try:
                header = SlottedPage.read_header(frame.data)
                if rid.slot < 0 or rid.slot >= header.slot_count:
                    return None
                data = SlottedPage.read_slot(frame.data, rid.slot)
                if data is None:
                    return None
                return Row(rid, list(RecordCodec.decode(schema, data)))
            finally:
                self._buffer.unpin(frame, False)

    def allocate_rid(self, table: str) -> RID:
        with self._lock:
            frame, slot = self._find_slot(table)
            rid = RID(table, frame.page_id, slot)
            self._reserved.add(rid)
            self._buffer.unpin(frame, False)
            return rid

    def release_rid(self, rid: RID) -> None:
        with self._lock:
            self._reserved.discard(rid)

    def insert(
        self,
        table: str,
        rid: RID,
        values: tuple[Value, ...],
        lsn: LSN | None,
    ) -> None:
        self._check_rid_table(table, rid)
        schema = self._catalog.get_table_schema(table)
        normalized = RecordCodec.validate(schema, values)
        if lsn is not None:
            self._check_primary_key(table, normalized, rid)
            with self._lock:
                if rid not in self._reserved:
                    raise StorageError(f"RID {rid} belum dipesan allocate_rid")
        data = RecordCodec.encode(schema, normalized)
        changed = False
        with self._lock:
            try:
                frame = self._buffer.get_page(table, rid.page_id)
            except (StorageError, KeyError):
                if lsn is not None:
                    raise StorageError(f"page {table}:{rid.page_id} tidak ada") from None
                # Upsert undo/redo: page pernah ada saat log ditulis (mis. sebelum crash).
                self._ensure_page(table, rid.page_id, schema)
                frame = self._buffer.get_page(table, rid.page_id)
            try:
                existing = SlottedPage.read_slot(frame.data, rid.slot)
                changed = existing != data
                if changed:
                    SlottedPage.write_slot(frame.data, rid.slot, data)
                    if lsn is not None:
                        SlottedPage.set_page_lsn(frame.data, lsn)
            except Exception:
                self._buffer.unpin(frame, False)
                raise
            self._reserved.discard(rid)
            self._buffer.unpin(frame, changed, lsn)
        if changed:
            self._index_manager.on_insert(table, normalized, rid)
            self._stats.on_insert(table)

    def update(self, table: str, rid: RID, values: tuple[Value, ...], lsn: LSN) -> None:
        return None

    def delete(self, table: str, rid: RID, lsn: LSN) -> None:
        return None

    def truncate(self, table: str) -> int:
        return 0

    def _check_primary_key(
        self,
        table: str,
        values: tuple[Value, ...],
        rid: RID | None = None,
    ) -> None:
        schema = self._catalog.get_table_schema(table)
        if not schema.primary_key:
            return
        positions = [schema.column_index(name) for name in schema.primary_key]
        key = tuple(values[i] for i in positions)
        for row in self.scan(table):
            if rid is not None and row.rid == rid:
                continue
            if tuple(row.values[i] for i in positions) == key:
                raise SchemaViolationError(
                    f"primary key {key} sudah ada di tabel {table}"
                )

    @staticmethod
    def _check_rid_table(table: str, rid: RID) -> None:
        if rid.table != table:
            raise StorageError(f"RID {rid} bukan milik tabel {table}")

    def _ensure_page(self, table: str, page_id: int, schema: TableSchema) -> None:
        """Buat page-page kosong sampai page_id ada (upsert undo/redo, D9)."""
        record_size = RecordCodec.max_record_size(schema)
        while self._buffer._disk.num_pages(table) <= page_id:
            frame = self._buffer.new_page(table, record_size)
            self._buffer.unpin(frame, False)

    def _find_slot(self, table: str) -> tuple[Frame, int]:
        schema = self._catalog.get_table_schema(table)
        record_size = RecordCodec.max_record_size(schema)
        page_count = self._buffer._disk.num_pages(table)
        if page_count:
            page_id = page_count - 1
            frame = self._buffer.get_page(table, page_id)
            slot = self._first_free_slot(frame, table, page_id, record_size)
            if slot is not None:
                self._last_page[table] = page_id
                return frame, slot
            self._buffer.unpin(frame, False)
        frame = self._buffer.new_page(table, record_size)
        slot = self._first_free_slot(frame, table, frame.page_id, record_size)
        if slot is None:
            self._buffer.unpin(frame, False)
            raise StorageError(f"page baru {table} tidak punya slot kosong")
        self._last_page[table] = frame.page_id
        return frame, slot

    def _first_free_slot(
        self,
        frame: Frame,
        table: str,
        page_id: int,
        record_size: int,
    ) -> int | None:
        # capacity 0 hanya terjadi saat SlottedPage (SM-01) masih stub.
        for slot in range(max(1, SlottedPage.capacity(record_size))):
            if SlottedPage.slot_status(frame.data, slot) != SlotStatus.EMPTY:
                continue
            if RID(table, page_id, slot) in self._reserved:
                continue
            return slot
        return None
