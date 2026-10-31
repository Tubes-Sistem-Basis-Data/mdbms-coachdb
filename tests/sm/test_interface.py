from __future__ import annotations

import inspect
import tempfile
import unittest

from common.interfaces.storage_manager import StorageManagerAPI
from mocks.mock_storage_manager import MockStorageManager
from storage_manager.facade import StorageManager

PLAN_METHODS = {
    "list_tables", "get_table_schema", "get_stats", "list_indexes",
    "validate", "scan", "fetch", "insert", "insert_at", "update", "delete", "truncate",
    "create_index", "index_lookup", "index_range", "rebuild_indexes",
    "attach_wal", "flush_all", "snapshot_to", "restore_from", "close",
}


class TestStorageManagerAPI(unittest.TestCase):
    def test_abstract_methods_match_plan(self):
        self.assertEqual(StorageManagerAPI.__abstractmethods__, PLAN_METHODS)

    def test_implementations_follow_signatures(self):
        for impl in (StorageManager, MockStorageManager):
            self.assertTrue(issubclass(impl, StorageManagerAPI), impl)
            for name in PLAN_METHODS:
                with self.subTest(impl=impl.__name__, method=name):
                    expected = inspect.signature(getattr(StorageManagerAPI, name))
                    actual = inspect.signature(getattr(impl, name))
                    self.assertEqual(list(actual.parameters), list(expected.parameters))

    def test_instantiable(self):
        MockStorageManager()
        with tempfile.TemporaryDirectory() as tmp:
            sm = StorageManager(tmp, 4)
        for attr in ("_lock", "_records", "_disk", "_buffer", "_catalog", "_stats", "_index_manager"):
            self.assertTrue(hasattr(sm, attr), attr)
        self.assertTrue(hasattr(StorageManager, "bulk_load"))


if __name__ == "__main__":
    unittest.main()
