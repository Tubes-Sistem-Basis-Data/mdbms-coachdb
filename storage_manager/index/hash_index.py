from __future__ import annotations

from common.storage import RID
from storage_manager.index.base import Index, Key
from storage_manager.types import Value

DEFAULT_BUCKETS = 64


class HashIndex(Index):
    def __init__(
        self, table: str, column: str, n_buckets: int = DEFAULT_BUCKETS
    ) -> None:
        super().__init__(table, column)
        self._n_buckets: int = n_buckets
        self._buckets: list[dict[Key, list[RID]]] = [{} for _ in range(n_buckets)]

    def _bucket(self, key: Key) -> dict[Key, list[RID]]:
        return self._buckets[hash(key) % self._n_buckets]

    def insert(self, key: Value, rid: RID) -> None:
        if key is None:
            return None
        siblings = self._bucket(key).setdefault(key, [])
        if rid not in siblings:
            siblings.append(rid)
        return None

    def delete(self, key: Value, rid: RID) -> None:
        if key is None:
            return None
        bucket = self._bucket(key)
        siblings = bucket.get(key)
        if siblings is None:
            return None
        try:
            siblings.remove(rid)
        except ValueError:
            return None
        if not siblings:
            del bucket[key]
        return None

    def search(self, key: Value) -> list[RID]:
        if key is None:
            return []
        return list(self._bucket(key).get(key, []))

    def range(self, low: Value | None, high: Value | None) -> list[RID]:
        return []

    def height(self) -> int:
        return 0
