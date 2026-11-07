from __future__ import annotations

import os
import tempfile
import unittest

from common.config import BLOCK_SIZE
from common.errors import StorageError
from storage_manager.disk import DiskManager


class TestDiskManager(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dm = DiskManager(self.tmp.name)

    def tearDown(self) -> None:
        self.dm.close()

    def test_create_table_file(self):
        self.dm.create_table_file("student")
        self.assertEqual(self.dm.num_pages("student"), 0)
        self.assertTrue(os.path.exists(os.path.join(self.tmp.name, "student.db")))

    def test_allocate_and_read_page(self):
        self.dm.create_table_file("course")
        data = bytes([0xAB] * BLOCK_SIZE)

        # This is allocation for first page (page_id=0)
        page_id = self.dm.allocate_page("course", data)
        self.assertEqual(page_id, 0)
        self.assertEqual(self.dm.num_pages("course"), 1)

        # Re-read again to test consistency
        read_data = self.dm.read_page("course", 0)
        self.assertEqual(read_data, data)

    def test_write_page(self):
        self.dm.create_table_file("enroll")
        self.dm.allocate_page("enroll", bytes(BLOCK_SIZE))

        # Test write and read again to ensure it matches
        new_data = bytes([0xCD] * BLOCK_SIZE)
        self.dm.write_page("enroll", 0, new_data)

        read_data = self.dm.read_page("enroll", 0)
        self.assertEqual(read_data, new_data)

    def test_invalid_data_size_rejected(self):
        self.dm.create_table_file("student")
        bad_data = bytes(BLOCK_SIZE - 10)

        # Test invalid size on allocation
        with self.assertRaises(ValueError):
            self.dm.allocate_page("student", bad_data)

        with self.assertRaises(ValueError):
            valid_data = bytes(BLOCK_SIZE)
            self.dm.allocate_page("student", valid_data)
            self.dm.write_page("student", 0, bad_data)

    def test_sync_and_close(self):
        # Ensure sync and close can happen without exception
        self.dm.create_table_file("test")
        self.dm.allocate_page("test", bytes(BLOCK_SIZE))
        self.dm.sync("test")
        self.dm.sync()
        self.dm.close()
        
        # Ensure file operation actually failed after it is closed
        with self.assertRaises((KeyError, FileNotFoundError)):
            self.dm.num_pages("test")

    def test_read_non_existent_page_raises_storage_error(self):
        self.dm.create_table_file("empty_tbl")
        with self.assertRaises(StorageError):
            self.dm.read_page("empty_tbl", 0)

        with self.assertRaises(StorageError):
            self.dm.read_page("empty_tbl", -1)

    def test_allocate_page_auto_creates_table(self):
        # num_pages should return 0 for uncreated table
        self.assertEqual(self.dm.num_pages("auto_tbl"), 0)

        data = bytes([0x55] * BLOCK_SIZE)
        page_id = self.dm.allocate_page("auto_tbl", data)

        self.assertEqual(page_id, 0)
        self.assertEqual(self.dm.num_pages("auto_tbl"), 1)
        self.assertEqual(self.dm.read_page("auto_tbl", 0), data)

    def test_lazy_open_existing_table_file(self):
        self.dm.create_table_file("persisted")
        data = bytes([0x12] * BLOCK_SIZE)

        self.dm.allocate_page("persisted", data)
        self.dm.sync()

        # Create another DiskManager on the same directory
        new_dm = DiskManager(self.tmp.name)
        try:
            self.assertEqual(new_dm.num_pages("persisted"), 1)
            self.assertEqual(new_dm.read_page("persisted", 0), data)
        finally:
            new_dm.close()

    def test_operations_after_close_fail(self):
        self.dm.close()
        data = bytes(BLOCK_SIZE)
        with self.assertRaises(KeyError):
            self.dm.create_table_file("fail_tbl")

        with self.assertRaises(KeyError):
            self.dm.allocate_page("fail_tbl", data)

        with self.assertRaises(KeyError):
            self.dm.read_page("fail_tbl", 0)

        with self.assertRaises(KeyError):
            self.dm.write_page("fail_tbl", 0, data)

        with self.assertRaises(KeyError):
            self.dm.sync("fail_tbl")
            
        with self.assertRaises(KeyError):
            self.dm.sync()


if __name__ == "__main__":
    unittest.main()