"""SM-10/SM-11: index B+Tree dengan dukungan duplicate key.

Leaf menyimpan key unik beserta daftar RID untuk key tersebut, sehingga
duplicate key tidak memakan slot key baru. Leaf dihubungkan lewat ``next``
supaya range scan cukup menelusuri rantai leaf tanpa kembali ke root.
"""

from __future__ import annotations

from abc import ABC
from bisect import bisect_left, bisect_right
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
    """B+Tree in-memory. ``order`` adalah jumlah maksimum key per node.

    Invariant internal node: ``children[i]`` berisi key pada rentang
    ``[keys[i-1], keys[i])``. Karena key di dalam satu node unik, separator
    tidak pernah ambigu walaupun tabel memiliki banyak duplicate key.
    """

    def __init__(
        self, table: str, column: str, order: int = DEFAULT_ORDER
    ) -> None:
        super().__init__(table, column)
        self._order: int = order
        self._root: BPlusNode = LeafNode()

    # ------------------------------------------------------------------ insert

    def insert(self, key: Value, rid: RID) -> None:
        if key is None:
            return None
        split = self._insert_node(self._root, key, rid)
        if split is not None:
            separator, right = split
            root = InternalNode()
            root.keys = [separator]
            root.children = [self._root, right]
            self._root = root
        return None

    def _insert_node(
        self, node: BPlusNode, key: Value, rid: RID
    ) -> Optional[tuple[Value, BPlusNode]]:
        if isinstance(node, LeafNode):
            position = bisect_left(node.keys, key)
            if position < len(node.keys) and node.keys[position] == key:
                siblings = node.values[position]
                if rid not in siblings:
                    siblings.append(rid)
            else:
                node.keys.insert(position, key)
                node.values.insert(position, [rid])
            if len(node.keys) > self._order:
                return self._split_leaf(node)
            return None

        assert isinstance(node, InternalNode)
        position = bisect_right(node.keys, key)
        split = self._insert_node(node.children[position], key, rid)
        if split is None:
            return None
        separator, right = split
        node.keys.insert(position, separator)
        node.children.insert(position + 1, right)
        if len(node.keys) > self._order:
            return self._split_internal(node)
        return None

    @staticmethod
    def _split_leaf(node: LeafNode) -> tuple[Value, LeafNode]:
        middle = len(node.keys) // 2
        right = LeafNode()
        right.keys = node.keys[middle:]
        right.values = node.values[middle:]
        node.keys = node.keys[:middle]
        node.values = node.values[:middle]
        right.next = node.next
        node.next = right
        # Separator = key terkecil di leaf kanan. Key di node kiri selalu
        # lebih kecil karena key dalam satu node unik.
        return right.keys[0], right

    @staticmethod
    def _split_internal(node: InternalNode) -> tuple[Value, InternalNode]:
        middle = len(node.keys) // 2
        separator = node.keys[middle]
        right = InternalNode()
        right.keys = node.keys[middle + 1:]
        right.children = node.children[middle + 1:]
        node.keys = node.keys[:middle]
        node.children = node.children[:middle + 1]
        return separator, right

    # ------------------------------------------------------------------ search

    def search(self, key: Value) -> list[RID]:
        if key is None:
            return []
        leaf = self._find_leaf(key)
        position = bisect_left(leaf.keys, key)
        if position < len(leaf.keys) and leaf.keys[position] == key:
            return list(leaf.values[position])
        return []

    def _find_leaf(self, key: Value) -> LeafNode:
        node = self._root
        while isinstance(node, InternalNode):
            position = bisect_right(node.keys, key)
            node = node.children[position]
        return node

    def _leftmost_leaf(self) -> LeafNode:
        node = self._root
        while isinstance(node, InternalNode):
            if not node.children:
                break
            node = node.children[0]
        if isinstance(node, LeafNode):
            return node
        # Tree kosong selalu berupa satu LeafNode; fallback ini hanya jaring
        # pengaman bila invariant rusak.
        return LeafNode()

    # ------------------------------------------------------------------- range

    def range(self, low: Optional[Value], high: Optional[Value]) -> list[RID]:
        if low is not None and high is not None and low > high:
            return []
        if isinstance(self._root, LeafNode) and not self._root.keys:
            return []

        leaf = self._leftmost_leaf() if low is None else self._find_leaf(low)
        position = 0 if low is None else bisect_left(leaf.keys, low)
        result: list[RID] = []
        while leaf is not None:
            for index in range(position, len(leaf.keys)):
                key = leaf.keys[index]
                if high is not None and key > high:
                    return result
                result.extend(leaf.values[index])
            leaf = leaf.next
            position = 0
        return result

    # ------------------------------------------------------------------ delete

    def delete(self, key: Value, rid: RID) -> None:
        if key is None:
            return None
        leaf = self._find_leaf(key)
        position = bisect_left(leaf.keys, key)
        if position >= len(leaf.keys) or leaf.keys[position] != key:
            return None
        siblings = leaf.values[position]
        try:
            siblings.remove(rid)
        except ValueError:
            return None
        if not siblings:
            del leaf.keys[position]
            del leaf.values[position]
        # Node yang menjadi kosong dibiarkan; separator lama tetap menjadi
        # panduan routing yang valid. Rebuild dilakukan saat startup/recovery.
        return None

    def height(self) -> int:
        height = 1
        node = self._root
        while isinstance(node, InternalNode):
            height += 1
            if not node.children:
                break
            node = node.children[0]
        return height
