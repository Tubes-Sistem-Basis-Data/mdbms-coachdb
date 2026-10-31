from __future__ import annotations

from typing import Callable, Iterator

from common.types import Row, TableStats
from storage_manager.catalog import Catalog


class StatisticsManager:
    def __init__(self, catalog: Catalog, scan_fn: Callable[[str], Iterator[Row]]) -> None:
        self._catalog = catalog
        self._scan_fn = scan_fn
        self._row_count: dict[str, int] = {}
        self._cache: dict[str, TableStats] = {}

    def get_stats(self, table: str) -> TableStats:
        raise NotImplementedError

    def on_insert(self, table: str) -> None:
        raise NotImplementedError

    def on_delete(self, table: str) -> None:
        raise NotImplementedError

    def invalidate(self, table: str) -> None:
        raise NotImplementedError

    def recount(self, table: str) -> None:
        raise NotImplementedError