from __future__ import annotations

import inspect
import tempfile
import unittest

from common.catalog import IndexInfo, TableSchema, TableStats
from common.interfaces import StorageManager as StorageManagerBase
from common.storage import RID
from storage_manager.facade import StorageManager


class TestStorageManagerContract(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.sm = StorageManager(self._tmp.name, 4)

    def tearDown(self):
        self._tmp.cleanup()

    def test_subclass_kontrak(self):
        self.assertTrue(issubclass(StorageManager, StorageManagerBase))
        self.assertFalse(inspect.isabstract(StorageManager))

    def test_signature_sama_dengan_kontrak(self):
        for name in StorageManagerBase.__abstractmethods__:
            with self.subTest(method=name):
                expected = inspect.signature(getattr(StorageManagerBase, name))
                actual = inspect.signature(getattr(StorageManager, name))
                self.assertEqual(list(actual.parameters), list(expected.parameters))

    def test_tipe_kembalian(self):
        sm = self.sm
        rid = RID("student", 0, 0)
        self.assertIsInstance(sm.list_tables(), list)
        self.assertIsInstance(sm.get_schema("student"), TableSchema)
        self.assertIsInstance(sm.get_stats("student"), TableStats)
        self.assertEqual(list(sm.scan("student")), [])
        self.assertIsNone(sm.fetch("student", rid))
        self.assertIsInstance(sm.allocate_rid("student"), RID)
        self.assertIsNone(sm.release_rid(rid))
        self.assertIsNone(sm.insert("student", rid, [1], 1))
        self.assertIsNone(sm.update("student", rid, [1], None))
        self.assertIsNone(sm.delete("student", rid, None))
        self.assertIsNone(sm.delete_all("student", None))
        self.assertIsNone(sm.create_index(IndexInfo("idx", "student", "id")))
        self.assertIsInstance(sm.index_lookup("student", "id", 1), list)
        self.assertIsInstance(sm.index_range("student", "id", None, 10), list)
        self.assertIsNone(sm.flush())

    def test_set_log_flusher_diteruskan_ke_buffer(self):
        def flusher(lsn: int) -> bool:
            return True

        self.sm.set_log_flusher(flusher)
        self.assertIs(self.sm._buffer._log_flusher, flusher)

    def test_komponen_internal_ada(self):
        for attr in ("_lock", "_records", "_disk", "_buffer", "_catalog", "_stats", "_index_manager"):
            self.assertTrue(hasattr(self.sm, attr), attr)


if __name__ == "__main__":
    unittest.main()
