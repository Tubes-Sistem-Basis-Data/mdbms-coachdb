"""Uji BufferPool dan hook WAL (SM-12, SM-13)."""

from __future__ import annotations

import inspect
import unittest
from unittest.mock import patch

from common import config
from common.errors import StorageError
from storage_manager.buffer import BufferPool, Frame

EXPECTED_SIGNATURES = {
    "set_log_flusher": ["self", "fn"],
    "get_page": ["self", "table", "page_id"],
    "new_page": ["self", "table", "record_size"],
    "unpin": ["self", "frame", "dirty", "lsn"],
    "flush_page": ["self", "table", "page_id"],
    "flush_table": ["self", "table"],
    "flush_all": ["self"],
    "discard_table": ["self", "table"],
    "_evict": ["self"],
    "_write_back": ["self", "frame"],
}


class FakeDisk:
    """DiskManager in-memory yang mencatat setiap operasi."""

    def __init__(self) -> None:
        self.pages: dict[tuple[str, int], bytes] = {}
        self.reads: list[tuple[str, int]] = []
        self.writes: list[tuple[str, int]] = []
        self.allocations: list[tuple[str, int]] = []
        self.syncs: list[str | None] = []
        self.events: list[str] = []

    def read_page(self, table: str, page_id: int) -> bytearray:
        self.reads.append((table, page_id))
        try:
            return bytearray(self.pages[(table, page_id)])
        except KeyError:
            raise StorageError(f"page {table}:{page_id} tidak ada") from None

    def write_page(self, table: str, page_id: int, data: bytes) -> None:
        self.pages[(table, page_id)] = bytes(data)
        self.writes.append((table, page_id))
        self.events.append("write")

    def num_pages(self, table: str) -> int:
        ids = [page_id for name, page_id in self.pages if name == table]
        return max(ids) + 1 if ids else 0

    def allocate_page(self, table: str, data: bytes) -> int:
        page_id = self.num_pages(table)
        self.pages[(table, page_id)] = bytes(data)
        self.allocations.append((table, page_id))
        self.events.append("allocate")
        return page_id

    def sync(self, table: str | None = None) -> None:
        self.syncs.append(table)


class FakePage:
    """SlottedPage palsu: init_page dan seal mencatat urutan kejadian."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.init_calls: list[tuple[int, int]] = []
        self.sealed: list[bytes] = []

    def init_page(self, page_id: int, record_size: int) -> bytearray:
        self.init_calls.append((page_id, record_size))
        self.events.append("init")
        data = bytearray(config.BLOCK_SIZE)
        data[0] = page_id % 256
        return data

    def seal(self, data: bytearray) -> None:
        data[-1] = 0xAA
        self.sealed.append(bytes(data))
        self.events.append("seal")


class FakeWal:
    """FRM palsu yang mencatat LSN yang di-flush."""

    def __init__(self, events: list[str] | None = None) -> None:
        self.flushed: list[int] = []
        self.events = events if events is not None else []

    def flush_log(self, page_lsn: int) -> bool:
        self.flushed.append(page_lsn)
        self.events.append("wal")
        return True


class FailingWal:
    """FRM palsu yang gagal saat flush log."""

    def __init__(self) -> None:
        self.flushed: list[int] = []

    def flush_log(self, page_lsn: int) -> bool:
        self.flushed.append(page_lsn)
        raise StorageError("log belum permanen")


class PoolTestCase(unittest.TestCase):
    """Siapkan disk, page codec, dan buffer pool baru untuk tiap test."""

    def setUp(self) -> None:
        self.page = FakePage()
        patcher = patch("storage_manager.buffer._slotted_page", return_value=self.page)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.disk = FakeDisk()
        self.disk.events = self.page.events
        self.pool = BufferPool(self.disk, capacity=2)

    def simpan_page(self, table: str, page_id: int) -> None:
        self.disk.pages[(table, page_id)] = bytes(config.BLOCK_SIZE)


class TestFrame(unittest.TestCase):
    def test_nilai_awal(self):
        data = bytearray(config.BLOCK_SIZE)
        frame = Frame("student", 3, data)
        self.assertEqual(frame.table, "student")
        self.assertEqual(frame.page_id, 3)
        self.assertIs(frame.data, data)
        self.assertEqual(frame.pin_count, 0)
        self.assertFalse(frame.dirty)
        self.assertEqual(frame.page_lsn, 0)

    def test_tiap_frame_mandiri(self):
        first = Frame("student", 0, bytearray(8))
        second = Frame("student", 1, bytearray(8))
        first.dirty = True
        first.pin_count += 1
        first.page_lsn = 9
        self.assertFalse(second.dirty)
        self.assertEqual(second.pin_count, 0)
        self.assertEqual(second.page_lsn, 0)


class TestBufferPoolDasar(unittest.TestCase):
    def test_kapasitas_default_dari_config(self):
        pool = BufferPool(FakeDisk())
        self.assertEqual(pool._capacity, config.BUFFER_FRAMES)
        self.assertEqual(len(pool._frames), 0)
        self.assertIsNone(pool._log_flusher)

    def test_kapasitas_khusus(self):
        pool = BufferPool(FakeDisk(), capacity=3)
        self.assertEqual(pool._capacity, 3)

    def test_kapasitas_tidak_valid(self):
        for capacity in (0, -1):
            with self.subTest(capacity=capacity), self.assertRaises(StorageError):
                BufferPool(FakeDisk(), capacity=capacity)

    def test_signature_sesuai_kontrak(self):
        for name, parameters in EXPECTED_SIGNATURES.items():
            with self.subTest(method=name):
                signature = inspect.signature(getattr(BufferPool, name))
                self.assertEqual(list(signature.parameters), parameters)


class TestGetPage(PoolTestCase):
    def test_cache_miss_membaca_dari_disk(self):
        self.simpan_page("student", 0)
        frame = self.pool.get_page("student", 0)
        self.assertEqual(self.disk.reads, [("student", 0)])
        self.assertEqual(frame.pin_count, 1)
        self.assertFalse(frame.dirty)
        self.assertEqual(bytes(frame.data), self.disk.pages[("student", 0)])
        self.assertIs(self.pool._frames[("student", 0)], frame)

    def test_cache_hit_tidak_membaca_lagi(self):
        self.simpan_page("student", 0)
        first = self.pool.get_page("student", 0)
        second = self.pool.get_page("student", 0)
        self.assertIs(first, second)
        self.assertEqual(second.pin_count, 2)
        self.assertEqual(self.disk.reads, [("student", 0)])

    def test_page_tidak_ada_tidak_masuk_buffer(self):
        with self.assertRaises(StorageError):
            self.pool.get_page("student", 9)
        self.assertEqual(len(self.pool._frames), 0)

    def test_hit_memindahkan_posisi_lru(self):
        self.simpan_page("student", 0)
        self.simpan_page("student", 1)
        self.simpan_page("student", 2)
        for page_id in (0, 1):
            self.pool.unpin(self.pool.get_page("student", page_id), False)
        self.pool.get_page("student", 0)
        self.pool.unpin(self.pool.get_page("student", 0), False)
        self.pool.get_page("student", 2)
        self.assertIn(("student", 0), self.pool._frames)
        self.assertNotIn(("student", 1), self.pool._frames)


class TestUnpin(PoolTestCase):
    def test_dirty_menaikkan_page_lsn(self):
        self.simpan_page("student", 0)
        frame = self.pool.get_page("student", 0)
        self.pool.get_page("student", 0)
        self.pool.unpin(frame, True, 5)
        self.pool.unpin(frame, True, 3)
        self.assertTrue(frame.dirty)
        self.assertEqual(frame.page_lsn, 5)
        self.assertEqual(frame.pin_count, 0)

    def test_lsn_lebih_besar_menimpa(self):
        self.simpan_page("student", 0)
        frame = self.pool.get_page("student", 0)
        self.pool.unpin(frame, True, 4)
        self.pool.get_page("student", 0)
        self.pool.unpin(frame, True, 9)
        self.assertEqual(frame.page_lsn, 9)

    def test_bersih_dan_lsn_none_tidak_mengubah_frame(self):
        self.simpan_page("student", 0)
        frame = self.pool.get_page("student", 0)
        self.pool.unpin(frame, False, 7)
        self.assertFalse(frame.dirty)
        self.assertEqual(frame.page_lsn, 0)

        self.pool.get_page("student", 0)
        self.pool.unpin(frame, True, None)
        self.assertTrue(frame.dirty)
        self.assertEqual(frame.page_lsn, 0)

    def test_unpin_berlebih_ditolak(self):
        self.simpan_page("student", 0)
        frame = self.pool.get_page("student", 0)
        self.pool.unpin(frame, False)
        with self.assertRaises(StorageError):
            self.pool.unpin(frame, False)

    def test_frame_asing_ditolak(self):
        frame = Frame("student", 0, bytearray(config.BLOCK_SIZE))
        with self.assertRaises(StorageError):
            self.pool.unpin(frame, True, 1)


class TestEviction(PoolTestCase):
    def test_frame_paling_lama_dievict(self):
        self.pool = BufferPool(self.disk, capacity=1)
        self.simpan_page("student", 0)
        self.simpan_page("student", 1)
        first = self.pool.get_page("student", 0)
        self.pool.unpin(first, False)
        self.pool.get_page("student", 1)
        self.assertNotIn(("student", 0), self.pool._frames)
        self.assertIn(("student", 1), self.pool._frames)
        self.assertEqual(self.disk.writes, [])

    def test_frame_terpin_tidak_dievict(self):
        self.simpan_page("student", 0)
        self.simpan_page("student", 1)
        self.simpan_page("student", 2)
        self.pool.get_page("student", 0)
        self.pool.unpin(self.pool.get_page("student", 1), False)
        self.pool.get_page("student", 2)
        self.assertIn(("student", 0), self.pool._frames)
        self.assertNotIn(("student", 1), self.pool._frames)

    def test_semua_terpin_menghasilkan_buffer_penuh(self):
        self.simpan_page("student", 0)
        self.simpan_page("student", 1)
        self.pool.get_page("student", 0)
        self.pool.get_page("student", 1)
        with self.assertRaisesRegex(StorageError, "buffer penuh"):
            self.pool.get_page("student", 2)
        self.assertEqual(len(self.pool._frames), 2)

    def test_evict_mengembalikan_frame(self):
        self.simpan_page("student", 0)
        frame = self.pool.get_page("student", 0)
        self.pool.unpin(frame, False)
        self.assertIs(self.pool._evict(), frame)
        self.assertNotIn(("student", 0), self.pool._frames)

    def test_page_dirty_ditulis_saat_evict(self):
        self.pool = BufferPool(self.disk, capacity=1)
        self.simpan_page("student", 0)
        self.simpan_page("student", 1)
        frame = self.pool.get_page("student", 0)
        self.pool.unpin(frame, True, 4)
        self.pool.get_page("student", 1)
        self.assertEqual(self.disk.writes, [("student", 0)])
        self.assertFalse(frame.dirty)
        self.assertEqual(self.disk.pages[("student", 0)][-1], 0xAA)


class TestNewPage(PoolTestCase):
    def test_alokasi_page_pertama(self):
        frame = self.pool.new_page("student", 32)
        self.assertEqual(frame.table, "student")
        self.assertEqual(frame.page_id, 0)
        self.assertEqual(frame.pin_count, 1)
        self.assertFalse(frame.dirty)
        self.assertEqual(self.page.init_calls, [(0, 32)])
        self.assertEqual(self.disk.allocations, [("student", 0)])
        self.assertIs(self.pool._frames[("student", 0)], frame)

    def test_page_berikutnya_mendapat_id_baru(self):
        self.pool.unpin(self.pool.new_page("student", 32), False)
        frame = self.pool.new_page("student", 32)
        self.assertEqual(frame.page_id, 1)
        self.assertEqual(self.page.init_calls, [(0, 32), (1, 32)])

    def test_alokasi_tidak_sesuai_ditolak(self):
        class SalahAlokasi(FakeDisk):
            def allocate_page(self, table: str, data: bytes) -> int:
                self.pages[(table, 5)] = bytes(data)
                return 5

        self.pool._disk = SalahAlokasi()
        with self.assertRaises(StorageError):
            self.pool.new_page("student", 32)
        self.assertEqual(len(self.pool._frames), 0)

    def test_page_baru_ditulis_setelah_dirty(self):
        frame = self.pool.new_page("student", 32)
        self.pool.unpin(frame, True, 3)
        self.pool.flush_page("student", 0)
        self.assertEqual(self.disk.writes, [("student", 0)])
        self.assertEqual(self.disk.syncs, ["student"])


class TestFlush(PoolTestCase):
    def test_urutan_wal_seal_tulis(self):
        self.pool = BufferPool(self.disk, capacity=1)
        wal = FakeWal(self.disk.events)
        self.pool.set_log_flusher(wal.flush_log)
        self.simpan_page("student", 0)
        self.simpan_page("student", 1)
        frame = self.pool.get_page("student", 0)
        self.pool.unpin(frame, True, 4)
        self.pool.get_page("student", 1)
        self.assertEqual(self.disk.events, ["wal", "seal", "write"])
        self.assertEqual(wal.flushed, [4])

    def test_gagal_flush_log_membatalkan_tulis(self):
        self.pool = BufferPool(self.disk, capacity=1)
        self.pool.set_log_flusher(FailingWal().flush_log)
        self.simpan_page("student", 0)
        self.simpan_page("student", 1)
        frame = self.pool.get_page("student", 0)
        self.pool.unpin(frame, True, 4)
        with self.assertRaises(StorageError):
            self.pool.get_page("student", 1)
        self.assertEqual(self.disk.writes, [])
        self.assertEqual(self.page.events, [])
        self.assertTrue(frame.dirty)
        self.assertIn(("student", 0), self.pool._frames)

    def test_flush_page_hanya_page_dirty(self):
        self.simpan_page("student", 0)
        self.simpan_page("student", 1)
        self.pool.unpin(self.pool.get_page("student", 0), False)
        frame = self.pool.get_page("student", 1)
        self.pool.unpin(frame, True, 2)
        self.pool.flush_page("student", 1)
        self.assertEqual(self.disk.writes, [("student", 1)])
        self.assertEqual(self.disk.syncs, ["student"])
        self.assertFalse(frame.dirty)

    def test_flush_page_tidak_ada_tidak_menulis(self):
        self.pool.flush_page("student", 99)
        self.assertEqual(self.disk.writes, [])
        self.assertEqual(self.disk.syncs, [])

    def test_flush_table_hanya_tabel_itu(self):
        self.simpan_page("student", 0)
        self.simpan_page("course", 0)
        self.pool.unpin(self.pool.get_page("student", 0), True, 1)
        self.pool.unpin(self.pool.get_page("course", 0), True, 2)
        self.pool.flush_table("student")
        self.assertEqual(self.disk.writes, [("student", 0)])
        self.assertEqual(self.disk.syncs, ["student"])
        self.assertIn(("course", 0), self.pool._frames)

    def test_flush_all_menulis_semua_dan_sync(self):
        self.simpan_page("student", 0)
        self.simpan_page("course", 0)
        self.pool.unpin(self.pool.get_page("student", 0), True, 1)
        self.pool.unpin(self.pool.get_page("course", 0), True, 2)
        self.pool.flush_all()
        self.assertEqual(sorted(self.disk.writes), [("course", 0), ("student", 0)])
        self.assertEqual(self.disk.syncs, [None])
        self.assertTrue(all(not frame.dirty for frame in self.pool._frames.values()))

    def test_flush_all_tanpa_page_dirty_tidak_sync(self):
        self.pool.flush_all()
        self.assertEqual(self.disk.writes, [])
        self.assertEqual(self.disk.syncs, [])

    def test_gagal_flush_log_saat_flush_page(self):
        self.pool.set_log_flusher(FailingWal().flush_log)
        self.simpan_page("student", 0)
        frame = self.pool.get_page("student", 0)
        self.pool.unpin(frame, True, 3)
        with self.assertRaises(StorageError):
            self.pool.flush_page("student", 0)
        self.assertEqual(self.disk.writes, [])
        self.assertTrue(frame.dirty)


class TestDiscardTable(PoolTestCase):
    def test_buang_tanpa_menulis(self):
        self.simpan_page("student", 0)
        self.simpan_page("course", 0)
        self.pool.unpin(self.pool.get_page("student", 0), True, 1)
        self.pool.unpin(self.pool.get_page("course", 0), True, 2)
        self.pool.discard_table("student")
        self.assertNotIn(("student", 0), self.pool._frames)
        self.assertIn(("course", 0), self.pool._frames)
        self.assertEqual(self.disk.writes, [])

    def test_tabel_tidak_ada_tidak_error(self):
        self.pool.discard_table("student")
        self.assertEqual(len(self.pool._frames), 0)


class TestWalHook(PoolTestCase):
    def test_set_log_flusher_menyimpan_callback(self):
        wal = FakeWal()
        self.pool.set_log_flusher(wal.flush_log)
        self.assertEqual(self.pool._log_flusher, wal.flush_log)

    def test_tanpa_wal_tidak_melempar_error(self):
        self.pool._flush_log_until(7)

    def test_flush_log_until_diteruskan_ke_frm(self):
        wal = FakeWal()
        self.pool.set_log_flusher(wal.flush_log)
        self.pool._flush_log_until(11)
        self.pool._flush_log_until(12)
        self.assertEqual(wal.flushed, [11, 12])

    def test_kegagalan_wal_tidak_ditelan(self):
        self.pool.set_log_flusher(FailingWal().flush_log)
        with self.assertRaises(StorageError):
            self.pool._flush_log_until(5)


if __name__ == "__main__":
    unittest.main()
