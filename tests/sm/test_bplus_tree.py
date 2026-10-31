import unittest

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


class TestBPlusTree(unittest.TestCase):
    def test_index_is_abstract(self):
        with self.assertRaises(TypeError):
            Index("t", "c")

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


if __name__ == "__main__":
    unittest.main()
