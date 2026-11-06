import unittest
import struct

from common.config import BLOCK_SIZE
from common.errors import StorageError
from storage_manager.page import SlottedPage, SlotStatus

class TestSlottedPage(unittest.TestCase):

    def test_init_page_and_header(self):
        buf = SlottedPage.init_page(page_id=5, record_size=30)
        hdr = SlottedPage.read_header(buf)
        
        self.assertEqual(hdr.page_id, 5)
        self.assertEqual(hdr.slot_count, 0)
        self.assertEqual(hdr.record_size, 30)
        self.assertEqual(hdr.page_lsn, 0)
        
        free_space = struct.unpack_from(SlottedPage.FREE_SPACE_PTR_FORMAT, buf, SlottedPage.FREE_SPACE_PTR_OFFSET)[0]
        self.assertEqual(free_space, BLOCK_SIZE)

    def test_variable_length_write_and_read(self):
        buf = SlottedPage.init_page(page_id=0, record_size=20)
        
        rec0 = b"short" # 5 bytes
        rec1 = b"a_much_longer_string_payload_for_testing" # 40 bytes
        rec2 = b"\x01\x02\x03\x04" # 4 bytes
        
        SlottedPage.write_slot(buf, 0, rec0)
        SlottedPage.write_slot(buf, 1, rec1)
        SlottedPage.write_slot(buf, 2, rec2)
        
        # Header validation check
        hdr = SlottedPage.read_header(buf)
        self.assertEqual(hdr.slot_count, 3)
        
        # Re-read and check is it match
        self.assertEqual(SlottedPage.read_slot(buf, 0), rec0)
        self.assertEqual(SlottedPage.read_slot(buf, 1), rec1)
        self.assertEqual(SlottedPage.read_slot(buf, 2), rec2)
        
        # Status validation
        self.assertEqual(SlottedPage.slot_status(buf, 1), SlotStatus.LIVE)


    def test_out_of_space_collision_prevention(self):
        buf = SlottedPage.init_page(page_id=0, record_size=50)
        
        max_safe_payload = BLOCK_SIZE - SlottedPage.DIR_START_OFFSET - SlottedPage.SLOT_DIR_ENTRY_SIZE
        
        # Check the case of not enough space during write_slot
        massive_payload = b"X" * (max_safe_payload + 1)
        with self.assertRaisesRegex(ValueError, "Not enough space"):
            SlottedPage.write_slot(buf, 0, massive_payload)

    def test_tombstone(self):
        buf = SlottedPage.init_page(page_id=0, record_size=10)
        SlottedPage.write_slot(buf, 0, b"data")
        
        SlottedPage.mark_tombstone(buf, 0)
        
        self.assertEqual(SlottedPage.slot_status(buf, 0), SlotStatus.TOMBSTONE)
        self.assertIsNone(SlottedPage.read_slot(buf, 0))

    def test_set_page_lsn(self):
        buf = SlottedPage.init_page(page_id=0, record_size=10)
        SlottedPage.set_page_lsn(buf, 999)
        hdr = SlottedPage.read_header(buf)
        self.assertEqual(hdr.page_lsn, 999)

    def test_checksum_seal_and_verify(self):
        buf = SlottedPage.init_page(page_id=1, record_size=15)
        SlottedPage.write_slot(buf, 0, b"test payload")
        
        # Checksum is invalid before sealing
        self.assertFalse(SlottedPage.verify(buf)) 
        
        # Checksum must be valid after sealing
        SlottedPage.seal(buf)
        self.assertTrue(SlottedPage.verify(buf))
        
        # Checksum validation must failed after tampering
        buf[-1] ^= 0xFF
        self.assertFalse(SlottedPage.verify(buf))

    def test_capacity_and_first_never_used_slot(self):
        hint_size = 100
        buf = SlottedPage.init_page(page_id=0, record_size=hint_size)
        
        usable_size = BLOCK_SIZE - SlottedPage.DIR_START_OFFSET
        expected_cap = usable_size // (SlottedPage.SLOT_DIR_ENTRY_SIZE + hint_size)
        
        self.assertEqual(SlottedPage.capacity(hint_size), expected_cap)
        self.assertEqual(SlottedPage.first_never_used_slot(buf), 0)

    def test_slot_bounds_checking(self):
        buf = SlottedPage.init_page(page_id=0, record_size=30)
        SlottedPage.write_slot(buf, 0, b"slot0_data")

        # Negative slot numbers
        self.assertIsNone(SlottedPage.read_slot(buf, -1))
        self.assertEqual(SlottedPage.slot_status(buf, -1), SlotStatus.EMPTY)
        with self.assertRaises(IndexError):
            SlottedPage.mark_tombstone(buf, -1)
        with self.assertRaises(IndexError):
            SlottedPage.write_slot(buf, -1, b"negative")

        # Slot numbers beyond slot_count
        self.assertIsNone(SlottedPage.read_slot(buf, 100))
        self.assertEqual(SlottedPage.slot_status(buf, 100), SlotStatus.EMPTY)
        with self.assertRaises(IndexError):
            SlottedPage.mark_tombstone(buf, 100)
    
    def test_capacity_validation(self):
        # Nilai tidak valid, harusnya raise StorageError
        for invalid_val in [0, -1, -50, "100", 12.5, True, False, None]:
            with self.subTest(val=invalid_val), self.assertRaises(StorageError):
                SlottedPage.capacity(invalid_val)

        # Record size yang sangat besar (melebihi ukuran blok) menghasilkan kapasitas 0
        self.assertEqual(SlottedPage.capacity(BLOCK_SIZE * 2), 0)


if __name__ == "__main__":
    unittest.main()