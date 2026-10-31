"""Facade Storage Manager: fungsi kosong."""

from __future__ import annotations

from typing import Callable, Iterator

from common.catalog import IndexInfo, TableSchema, TableStats
from common.interfaces.storage_manager import StorageManager as StorageManagerAPI
from common.storage import RID, Row


class StorageManager(StorageManagerAPI):
    """Facade sementara Storage Manager."""

    def __init__(self, data_dir: str = "", buffer_frames: int = 0) -> None:
        self._data_dir = data_dir
        self._buffer_frames = buffer_frames

    def list_tables(self) -> list[str]:
        return []

    def get_table_schema(self, table: str) -> TableSchema:
        return None

    def get_schema(self, table: str) -> TableSchema:
        return None

    def get_stats(self, table: str) -> TableStats:
        return None

    def list_indexes(self, table: str | None = None) -> list[IndexInfo]:
        return []

    def validate(self, table: str, values: list) -> list:
        return []

    def scan(self, table: str) -> Iterator[Row]:
        return iter(())

    def fetch(self, table: str, rid: RID) -> Row | None:
        return None

    def allocate_rid(self, table: str) -> RID:
        return None

    def release_rid(self, rid: RID) -> None:
        return None

    def insert(self, table: str, values=None, log_fn: Callable[[RID], int] | None = None, lsn: int | None = None) -> int:
        return 0

    def insert_at(self, table: str, rid: RID, values: list, lsn: int | None) -> None:
        return None

    def update(self, table: str, rid: RID, values: list, lsn: int | None) -> None:
        return None

    def delete(self, table: str, rid: RID, lsn: int | None) -> None:
        return None

    def delete_all(self, table: str, lsn: int | None) -> None:
        return None

    def truncate(self, table: str) -> int:
        return 0

    def bulk_load(self, table: str, rows: list[tuple]) -> int:
        return 0

    def create_index(self, info: IndexInfo) -> None:
        return None

    def index_lookup(self, table: str, column: str, key) -> list[RID]:
        return []

    def index_range(self, table: str, column: str, low, high) -> list[RID]:
        return []

    def rebuild_indexes(self) -> None:
        return None

    def flush(self, page_ids: list[int] | None = None) -> None:
        return None

    def flush_all(self) -> None:
        return None

    def set_log_flusher(self, fn: Callable[[int], bool]) -> None:
        return None

    def attach_wal(self, frm) -> None:
        return None

    def snapshot_to(self, path: str) -> None:
        return None

    def restore_from(self, path: str) -> None:
        return None

    def close(self) -> None:
        return None
