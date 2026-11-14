from __future__ import annotations

from bisect import bisect_left, bisect_right
from typing import Any, cast

from common.storage import RID
from storage_manager.index.base import Index, Key
from storage_manager.types import Value

DEFAULT_ORDER = 4


def _bisect_left(keys: list[Key], key: Key) -> int:
    return bisect_left(cast(Any, keys), key)


def _bisect_right(keys: list[Key], key: Key) -> int:
    return bisect_right(cast(Any, keys), key)


def _greater(left: Any, right: Any) -> bool:
    return left > right


class BPlusNode:
    def __init__(self) -> None:
        self.keys: list[Key] = []


class InternalNode(BPlusNode):
    def __init__(self) -> None:
        super().__init__()
        self.children: list[BPlusNode] = []


class LeafNode(BPlusNode):
    def __init__(self) -> None:
        super().__init__()
        self.values: list[list[RID]] = []
        self.next: LeafNode | None = None


class BPlusTree(Index):
    def __init__(
        self, table: str, column: str, order: int = DEFAULT_ORDER
    ) -> None:
        super().__init__(table, column)
        self._order: int = order
        self._root: BPlusNode = LeafNode()

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
        self, node: BPlusNode, key: Key, rid: RID
    ) -> tuple[Key, BPlusNode] | None:
        if isinstance(node, LeafNode):
            position = _bisect_left(node.keys, key)
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
        position = _bisect_right(node.keys, key)
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
    def _split_leaf(node: LeafNode) -> tuple[Key, LeafNode]:
        middle = len(node.keys) // 2
        right = LeafNode()
        right.keys = node.keys[middle:]
        right.values = node.values[middle:]
        node.keys = node.keys[:middle]
        node.values = node.values[:middle]
        right.next = node.next
        node.next = right
        return right.keys[0], right

    @staticmethod
    def _split_internal(node: InternalNode) -> tuple[Key, InternalNode]:
        middle = len(node.keys) // 2
        separator = node.keys[middle]
        right = InternalNode()
        right.keys = node.keys[middle + 1:]
        right.children = node.children[middle + 1:]
        node.keys = node.keys[:middle]
        node.children = node.children[:middle + 1]
        return separator, right

    def search(self, key: Value) -> list[RID]:
        if key is None:
            return []
        try:
            leaf = self._find_leaf(key)
            position = _bisect_left(leaf.keys, key)
            if position < len(leaf.keys) and leaf.keys[position] == key:
                return list(leaf.values[position])
        except TypeError:
            return []
        return []

    def _find_leaf(self, key: Key) -> LeafNode:
        node = self._root
        while isinstance(node, InternalNode):
            position = _bisect_right(node.keys, key)
            node = node.children[position]
        assert isinstance(node, LeafNode)
        return node

    def _leftmost_leaf(self) -> LeafNode:
        node = self._root
        while isinstance(node, InternalNode):
            if not node.children:
                break
            node = node.children[0]
        if isinstance(node, LeafNode):
            return node
        return LeafNode()

    def range(self, low: Value | None, high: Value | None) -> list[RID]:
        try:
            if low is not None and high is not None and _greater(low, high):
                return []
        except TypeError:
            return []
        if isinstance(self._root, LeafNode) and not self._root.keys:
            return []

        try:
            leaf: LeafNode | None = (
                self._leftmost_leaf() if low is None else self._find_leaf(low)
            )
            position = 0 if low is None else _bisect_left(leaf.keys, low)
            result: list[RID] = []
            while leaf is not None:
                for index in range(position, len(leaf.keys)):
                    key = leaf.keys[index]
                    if high is not None and _greater(key, high):
                        return result
                    result.extend(leaf.values[index])
                leaf = leaf.next
                position = 0
            return result
        except TypeError:
            return []

    def delete(self, key: Value, rid: RID) -> None:
        if key is None:
            return None
        try:
            leaf = self._find_leaf(key)
            position = _bisect_left(leaf.keys, key)
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
        except TypeError:
            return None
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
