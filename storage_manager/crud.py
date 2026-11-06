"""Record Manager: fungsi kosong."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Callable, Iterator, Optional

from common.errors import StorageError
from common.storage import RID, Row
from storage_manager.buffer import Frame
from storage_manager.page import SlottedPage
from storage_manager.serializer import RecordCodec
from storage_manager.types import LSN, Value

if TYPE_CHECKING:
    from storage_manager.buffer import BufferPool
    from storage_manager.catalog import Catalog
    from storage_manager.index import IndexManager
    from storage_manager.stats import StatisticsManager


class RecordManager:
    """Record manager sementara."""

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
        self._buffer = buffer
        self._catalog = catalog
        self._index_manager = index_manager
        self._stats = stats

    def validate(self, table: str, values: tuple[Value, ...]) -> tuple[Value, ...]:
        return ()

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
            except StorageError:
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

    def insert(
        self,
        table: str,
        values: tuple[Value, ...],
        log_fn: Callable[[RID], LSN],
    ) -> RID:
        return RID(table=table, page_id=0, slot=0)

    def insert_at(self, table: str, rid: RID, values: tuple[Value, ...], lsn: LSN) -> None:
        return None

    def update(self, table: str, rid: RID, values: tuple[Value, ...], lsn: LSN) -> None:
        return None

    def delete(self, table: str, rid: RID, lsn: LSN) -> None:
        return None

    def truncate(self, table: str) -> int:
        return 0

    def _check_primary_key(self, table: str, values: tuple[Value, ...]) -> None:
        return None

    @staticmethod
    def _check_rid_table(table: str, rid: RID) -> None:
        if rid.table != table:
            raise StorageError(f"RID {rid} bukan milik tabel {table}")

    def _find_slot(self, table: str) -> tuple[Frame, int]:
        return Frame(table=table, page_id=0, data=bytearray()), 0
