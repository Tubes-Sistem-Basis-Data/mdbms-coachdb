"""SM-10/SM-11: index Hash untuk pencarian kesamaan.

Hash hanya melayani ``search`` (equality). ``range`` mengembalikan kosong dan
QO tidak pernah memilih Hash untuk predicate rentang; validasi ada di QO/QP.
"""

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

    def _bucket(self, key: Value) -> dict:
        if not isinstance(key, (int, float, str, bytes, tuple)):
            raise IndexDefinitionError(f"Key tidak bisa di-hash: {key!r}")
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

    def range(self, low: Optional[Value], high: Optional[Value]) -> list[RID]:
        """Hash tidak mendukung rentang; selalu kosong."""
        return []

    def height(self) -> int:
        """Hash bukan tree, jadi tidak punya tinggi node."""
        return 0
