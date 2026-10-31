from __future__ import annotations

from abc import ABC
from typing import Optional

from common.storage import RID
from storage_manager.index.manager import Index, Value

DEFAULT_ORDER = 4


class BPlusNode(ABC):
    def __init__(self) -> None:
        self.keys: list[Value] = []


class InternalNode(BPlusNode):
    def __init__(self) -> None:
        super().__init__()
        self.children: list[BPlusNode] = []


class LeafNode(BPlusNode):
    def __init__(self) -> None:
        super().__init__()
        self.values: list[list[RID]] = []
        self.next: Optional[LeafNode] = None


class BPlusTree(Index):
    def __init__(
        self, table: str, column: str, order: int = DEFAULT_ORDER
    ) -> None:
        super().__init__(table, column)
        self._order: int = order
        self._root: BPlusNode = LeafNode()

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
