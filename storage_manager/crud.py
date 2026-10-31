"""Record Manager: fungsi kosong."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Callable, Iterator, Optional

from common.storage import RID, Row
from storage_manager.types import LSN, Value

if TYPE_CHECKING:
    from storage_manager.buffer import BufferPool, Frame
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
        return iter(())

    def fetch(self, table: str, rid: RID) -> Optional[Row]:
        return None

    def insert(
        self,
        table: str,
        values: tuple[Value, ...],
        log_fn: Callable[[RID], LSN],
    ) -> RID:
        return None

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

    def _find_slot(self, table: str) -> tuple[Frame, int]:
        return None
