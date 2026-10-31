"""Record Manager: fungsi kosong."""

from __future__ import annotations

from typing import Any, Iterator

from common.storage import RID, Row


class RecordManager:
    """Record manager sementara."""

    def __init__(
        self,
        lock: Any,
        buffer: Any,
        catalog: Any,
        index_manager: Any,
        stats: Any,
    ) -> None:
        self._lock = lock
        self._buffer = buffer
        self._catalog = catalog
        self._index_manager = index_manager
        self._stats = stats

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

    def insert(self, table: str, rid: RID, values: list, lsn: int | None) -> None:
        return None

    def insert_at(self, table: str, rid: RID, values: list, lsn: int) -> None:
        return None

    def update(self, table: str, rid: RID, values: list, lsn: int | None) -> None:
        return None

    def delete(self, table: str, rid: RID, lsn: int | None) -> None:
        return None

    def delete_all(self, table: str, lsn: int | None) -> None:
        return None

    def truncate(self, table: str) -> int:
        return 0

    def _check_primary_key(self, table: str, values: list, rid: RID | None = None) -> None:
        return None

    def _find_slot(self, table: str):
        return None
