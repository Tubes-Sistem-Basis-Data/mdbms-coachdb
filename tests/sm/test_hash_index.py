import unittest

from common.storage import RID
from storage_manager.index import HashIndex, Index


def rid(slot: int, table: str = "t") -> RID:
    return RID(table, 0, slot)


class TestHashIndex(unittest.TestCase):
    def test_hash_index_is_index(self):
        self.assertTrue(issubclass(HashIndex, Index))

    def test_hash_index_attributes(self):
        idx = HashIndex("student", "id", n_buckets=8)
        self.assertEqual(idx.table, "student")
        self.assertEqual(idx.column, "id")
        self.assertEqual(idx._n_buckets, 8)
        self.assertEqual(idx._buckets, [{} for _ in range(8)])

    def test_default_returns(self):
        cases = [
            ("insert", (1, RID("student", 0, 0)), None),
            ("delete", (1, RID("student", 0, 0)), None),
            ("search", (1,), []),
            ("range", (1, 10), []),
            ("height", (), 0),
        ]
        for name, args, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(getattr(HashIndex("student", "id"), name)(*args), expected)


class TestHashIndexOperations(unittest.TestCase):
    def setUp(self):
        self.index = HashIndex("t", "c", n_buckets=8)

    def test_insert_and_search_duplicate_keys(self):
        for slot in range(4):
            self.index.insert(7, rid(slot))
        self.assertEqual(self.index.search(7), [rid(i) for i in range(4)])
        self.assertEqual(self.index.search(8), [])

    def test_insert_is_idempotent_for_same_rid(self):
        self.index.insert(7, rid(0))
        self.index.insert(7, rid(0))
        self.assertEqual(self.index.search(7), [rid(0)])

    def test_collisions_in_single_bucket(self):
        index = HashIndex("t", "c", n_buckets=1)
        for key in range(20):
            index.insert(key, rid(key))
        for key in range(20):
            self.assertEqual(index.search(key), [rid(key)])

    def test_none_is_not_indexed(self):
        self.index.insert(None, rid(0))
        self.assertEqual(self.index.search(None), [])
        self.index.delete(None, rid(0))
        self.assertEqual(self.index.search(None), [])

    def test_delete_only_removes_requested_duplicate(self):
        for slot in range(3):
            self.index.insert(7, rid(slot))
        self.index.delete(7, rid(1))
        self.assertEqual(self.index.search(7), [rid(0), rid(2)])
        self.index.delete(7, rid(0))
        self.index.delete(7, rid(2))
        self.assertEqual(self.index.search(7), [])

    def test_delete_unknown_key_or_rid_is_noop(self):
        self.index.insert(7, rid(0))
        self.index.delete(8, rid(0))
        self.index.delete(7, rid(9))
        self.assertEqual(self.index.search(7), [rid(0)])

    def test_range_is_empty_because_hash_is_equality_only(self):
        self.index.insert(5, rid(0))
        self.assertEqual(self.index.range(0, 10), [])


if __name__ == "__main__":
    unittest.main()
