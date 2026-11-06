from __future__ import annotations

from copy import deepcopy
from typing import Callable, Iterator

from common.catalog import TableStats
from common.errors import StorageError
from common.storage import Row
from storage_manager.catalog import Catalog
from storage_manager.page import SlottedPage


class StatisticsManager:
    def __init__(self, catalog: Catalog, scan_fn: Callable[[str], Iterator[Row]]) -> None:
        self._catalog = catalog
        self._scan_fn = scan_fn
        self._row_count: dict[str, int] = {}
        self._cache: dict[str, TableStats] = {}

    def get_stats(self, table: str) -> TableStats:
        self._catalog.get_table_schema(table)
        if table not in self._cache:
            self.recount(table)
        return deepcopy(self._cache[table])

    def on_insert(self, table: str) -> None:
        # Called after a successful mutation. Re-scan lazily rather than applying
        # deltas that could double-count repeated recovery/upsert notifications.
        self.invalidate(table)

    def on_delete(self, table: str) -> None:
        self.invalidate(table)

    def invalidate(self, table: str) -> None:
        self._catalog.get_table_schema(table)
        self._cache.pop(table, None)
        self._row_count.pop(table, None)

    def recount(self, table: str) -> None:
        schema = self._catalog.get_table_schema(table)
        self.invalidate(table)
        size = self._catalog.get_record_size(table)
        distinct = [set() for _ in schema.columns]
        pages = set()
        count = 0
        for row in self._scan_fn(table):
            if row.rid.table != table or len(row.values) != len(schema.columns):
                raise StorageError(f"Baris scan tidak sesuai skema/RID: {table}")
            count += 1
            pages.add(row.rid.page_id)
            for values, value in zip(distinct, row.values):
                if value is not None:
                    values.add(value)
        stats = TableStats(table=table, n_r=count, b_r=len(pages), l_r=float(size),
                           f_r=SlottedPage.capacity(size),
                           distinct={col.name: len(values) for col, values in zip(schema.columns, distinct)})
        self._row_count[table] = count
        self._cache[table] = stats
