import unittest

from common.storage import RID
from storage_manager.index import HashIndex, Index


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


if __name__ == "__main__":
    unittest.main()
