"""Facade Storage Manager: fungsi kosong."""

from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING, Callable, Iterator, Optional

from common import config
from common.interfaces.storage_manager import StorageManagerAPI
from common.types import (
    LSN,
    RID,
    IndexInfo,
    IndexKind,
    Row,
    TableSchema,
    TableStats,
    Value,
)
from storage_manager.buffer import BufferPool
from storage_manager.catalog import Catalog
from storage_manager.crud import RecordManager
from storage_manager.disk import DiskManager
from storage_manager.index import IndexManager
from storage_manager.stats import StatisticsManager

if TYPE_CHECKING:
    from common.interfaces.failure_recovery import (
        FailureRecoveryManager as FailureRecoveryAPI,
    )

CATALOG_FILE = "catalog.json"


class StorageManager(StorageManagerAPI):
    """Facade sementara Storage Manager."""

    def __init__(
        self,
        data_dir: str = config.DATA_DIR,
        buffer_frames: int = config.BUFFER_FRAMES,
    ) -> None:
        self._lock = threading.RLock()
        self._disk = DiskManager(data_dir)
        self._buffer = BufferPool(self._disk, buffer_frames)
        self._catalog = Catalog(os.path.join(data_dir, CATALOG_FILE))
        self._stats = StatisticsManager(self._catalog, lambda t: self._records.scan(t))
        self._index_manager = IndexManager(self._catalog, lambda t: self._records.scan(t))
        self._records = RecordManager(
            self._lock, self._buffer, self._catalog, self._index_manager, self._stats
        )

    def list_tables(self) -> list[str]:
        return []

    def get_table_schema(self, table: str) -> TableSchema:
        return None

    def get_stats(self, table: str) -> TableStats:
        return None

    def list_indexes(self, table: Optional[str] = None) -> list[IndexInfo]:
        return []

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

    def bulk_load(self, table: str, rows: list[tuple]) -> int:
        return 0

    def create_index(self, name: str, table: str, column: str, kind: IndexKind) -> IndexInfo:
        return None

    def index_lookup(self, index_name: str, key: Value) -> list[RID]:
        return []

    def index_range(
        self,
        index_name: str,
        low: Optional[Value],
        high: Optional[Value],
    ) -> list[RID]:
        return []

    def rebuild_indexes(self) -> None:
        return None

    def flush_all(self) -> None:
        return None

    def attach_wal(self, frm: FailureRecoveryAPI) -> None:
        return None

    def snapshot_to(self, path: str) -> None:
        return None

    def restore_from(self, path: str) -> None:
        return None

    def close(self) -> None:
        return None
