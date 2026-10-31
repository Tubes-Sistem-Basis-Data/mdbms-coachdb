"""Mock Storage Manager: fungsi kosong."""

from __future__ import annotations

from typing import Callable, Iterator

from common.catalog import TableSchema, TableStats
from common.interfaces import StorageManager
from common.storage import RID, Row


class MockStorageManager(StorageManager):
    """Mock sementara untuk pengembangan komponen lain."""

    def __init__(self, data_dir: str | None = None) -> None:
        self.data_dir = data_dir

    def list_tables(self) -> list[str]:
        return []

    def get_schema(self, table: str) -> TableSchema:
        return None

    def get_stats(self, table: str) -> TableStats:
        return None

    def scan(self, table: str) -> Iterator[Row]:
        return iter(())

    def fetch(self, table: str, rid: RID) -> Row | None:
        return None

    def allocate_rid(self, table: str) -> RID:
        return None

    def release_rid(self, rid: RID) -> None:
        return None

    def insert(self, table: str, rid: RID, values: list, lsn: int | None) -> None:
        return None

    def update(self, table: str, rid: RID, values: list, lsn: int | None) -> None:
        return None

    def delete(self, table: str, rid: RID, lsn: int | None) -> None:
        return None

    def delete_all(self, table: str, lsn: int | None) -> None:
        return None

    def create_index(self, info) -> None:
        return None

    def index_lookup(self, table: str, column: str, key) -> list[RID]:
        return []

    def index_range(self, table: str, column: str, low, high) -> list[RID]:
        return []

    def flush(self, page_ids: list[int] | None = None) -> None:
        return None

    def set_log_flusher(self, fn: Callable[[int], bool]) -> None:
        return None
