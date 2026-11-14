import unittest

from common.storage import RID
from storage_manager.index import (
    BPlusNode,
    BPlusTree,
    Index,
    IndexManager,
    InternalNode,
    LeafNode,
)

INDEX_METHODS = ["insert", "delete", "search", "range", "height"]
MANAGER_METHODS = [
    "create_index", "lookup", "range", "height",
    "on_insert", "on_delete", "on_update", "rebuild_all",
]


def rid(slot: int, table: str = "t") -> RID:
    return RID(table, 0, slot)


class TestBPlusTree(unittest.TestCase):
    def test_index_is_abstract(self) -> None:
        with self.assertRaises(TypeError):
            type.__call__(Index, "t", "c")

    def test_bplustree_has_index_methods(self):
        for name in INDEX_METHODS:
            with self.subTest(name=name):
                self.assertTrue(callable(getattr(BPlusTree, name)))

    def test_index_manager_has_methods(self):
        for name in MANAGER_METHODS:
            with self.subTest(name=name):
                self.assertTrue(callable(getattr(IndexManager, name)))

    def test_bplustree_attributes(self):
        tree = BPlusTree("student", "id", order=4)
        self.assertEqual(tree.table, "student")
        self.assertEqual(tree.column, "id")
        self.assertEqual(tree._order, 4)
        self.assertIsInstance(tree._root, LeafNode)

    def test_node_hierarchy(self):
        self.assertTrue(issubclass(InternalNode, BPlusNode))
        self.assertTrue(issubclass(LeafNode, BPlusNode))
        leaf = LeafNode()
        self.assertEqual(leaf.keys, [])
        self.assertEqual(leaf.values, [])
        self.assertIsNone(leaf.next)
        self.assertEqual(InternalNode().children, [])

    def test_default_returns(self):
        self.assertEqual(BPlusTree("t", "c").search(1), [])
        self.assertEqual(BPlusTree("t", "c").height(), 1)


class TestBPlusTreeOperations(unittest.TestCase):
    tree: BPlusTree

    def setUp(self) -> None:
        self.tree = BPlusTree("t", "c", order=4)

    def test_insert_and_search_duplicate_keys(self):
        for slot in range(5):
            self.tree.insert(7, rid(slot))
        self.assertEqual(self.tree.search(7), [rid(i) for i in range(5)])
        self.assertEqual(self.tree.search(8), [])

    def test_insert_is_idempotent_for_same_rid(self):
        self.tree.insert(7, rid(0))
        self.tree.insert(7, rid(0))
        self.assertEqual(self.tree.search(7), [rid(0)])

    def test_none_is_not_indexed(self):
        self.tree.insert(None, rid(0))
        self.assertEqual(self.tree.search(None), [])
        self.assertEqual(self.tree.range(None, None), [])
        self.tree.delete(None, rid(0))
        self.assertEqual(self.tree.search(None), [])

    def test_split_and_height(self):
        for key in range(50):
            self.tree.insert(key, rid(key))
        self.assertGreater(self.tree.height(), 1)
        for key in range(50):
            self.assertEqual(self.tree.search(key), [rid(key)])

    def test_range_is_inclusive_and_ordered(self):
        for slot, key in enumerate((1, 2, 2, 3, 5)):
            self.tree.insert(key, rid(slot))
        self.assertEqual(self.tree.range(2, 3), [rid(1), rid(2), rid(3)])
        self.assertEqual(self.tree.range(None, 2), [rid(0), rid(1), rid(2)])
        self.assertEqual(self.tree.range(3, None), [rid(3), rid(4)])
        self.assertEqual(
            self.tree.range(None, None),
            [rid(0), rid(1), rid(2), rid(3), rid(4)],
        )
        self.assertEqual(self.tree.range(4, 3), [])
        self.assertEqual(self.tree.range(99, None), [])

    def test_range_across_leaves(self):
        keys = list(range(0, 100, 2))
        for key in keys:
            self.tree.insert(key, rid(key))
        actual = self.tree.range(20, 40)
        expected = [rid(key) for key in keys if 20 <= key <= 40]
        self.assertEqual(actual, expected)

    def test_delete_only_removes_requested_duplicate(self):
        for slot in range(3):
            self.tree.insert(7, rid(slot))
        self.tree.delete(7, rid(1))
        self.assertEqual(self.tree.search(7), [rid(0), rid(2)])

    def test_delete_last_key_then_reinsert(self):
        self.tree.insert(7, rid(0))
        self.tree.delete(7, rid(0))
        self.assertEqual(self.tree.search(7), [])
        self.tree.insert(7, rid(1))
        self.assertEqual(self.tree.search(7), [rid(1)])

    def test_delete_unknown_key_or_rid_is_noop(self):
        self.tree.insert(7, rid(0))
        self.tree.delete(8, rid(0))
        self.tree.delete(7, rid(9))
        self.assertEqual(self.tree.search(7), [rid(0)])

    def test_order_two_splits(self):
        tree = BPlusTree("t", "c", order=2)
        for key in (5, 6, 7, 8, 9, 10):
            tree.insert(key, rid(key))
        for key in (5, 6, 7, 8, 9, 10):
            self.assertEqual(tree.search(key), [rid(key)])
        self.assertEqual(tree.range(6, 9), [rid(6), rid(7), rid(8), rid(9)])

    def test_string_keys(self):
        for slot, key in enumerate(("GO", "IF", "ZZ", "IF")):
            self.tree.insert(key, rid(slot))
        self.assertEqual(self.tree.search("IF"), [rid(1), rid(3)])
        self.assertEqual(self.tree.range("GO", "IF"), [rid(0), rid(1), rid(3)])

    def test_incompatible_key_types(self):
        self.tree.insert(10, rid(0))
        self.assertEqual(self.tree.search("invalid"), [])
        self.assertEqual(self.tree.range("a", "z"), [])
        self.assertEqual(self.tree.range(1, "z"), [])
        self.tree.delete("invalid", rid(0))
        self.assertEqual(self.tree.search(10), [rid(0)])


if __name__ == "__main__":
    unittest.main()
