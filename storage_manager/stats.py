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
        return TableStats(table=table, n_r=0, b_r=0, l_r=0.0, f_r=0, distinct={})

    def on_insert(self, table: str) -> None:
        return None

    def on_delete(self, table: str) -> None:
        return None

    def invalidate(self, table: str) -> None:
        return None

    def recount(self, table: str) -> None:
        return None
