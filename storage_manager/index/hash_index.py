from __future__ import annotations

from typing import Optional

from common.storage import RID
from storage_manager.errors import IndexDefinitionError
from storage_manager.types import Value
from storage_manager.index.manager import Index

DEFAULT_BUCKETS = 64

class HashIndex(Index):
    def __init__(
        self, table: str, column: str, n_buckets: int = DEFAULT_BUCKETS
    ) -> None:
        super().__init__(table, column)
        self._n_buckets: int = n_buckets
        self._buckets: list[dict] = [{} for _ in range(n_buckets)]

    def insert(self, key: Value, rid: RID) -> None:
        return None

    def delete(self, key: Value, rid: RID) -> None:
        return None

    def search(self, key: Value) -> list[RID]:
        return []

    def range(self, low: Optional[Value], high: Optional[Value]) -> list[RID]:
        return []

    def height(self) -> int:
        return 0
