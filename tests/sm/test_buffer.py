"""Uji BufferPool dan hook WAL (SM-12, SM-13)."""

from __future__ import annotations

import inspect
import threading
import unittest
from unittest.mock import patch

from common import config
from common.errors import LogWriteError, StorageError
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
    "discard_all": ["self"],
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

    def test_flush_all_tanpa_page_dirty_tetap_sync(self):
        self.pool.flush_all()
        self.assertEqual(self.disk.writes, [])
        self.assertEqual(self.disk.syncs, [None])

    def test_flush_all_men_sync_tulisan_dari_penggantian_page(self):
        self.pool = BufferPool(self.disk, capacity=1)
        self.simpan_page("student", 0)
        self.simpan_page("student", 1)
        self.pool.unpin(self.pool.get_page("student", 0), True, 2)
        self.pool.get_page("student", 1)
        self.assertEqual(self.disk.writes, [("student", 0)])
        self.assertEqual(self.disk.syncs, [])
        self.pool.flush_all()
        self.assertEqual(self.disk.syncs, [None])

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

    def test_discard_all_buang_semua_tanpa_menulis(self):
        self.simpan_page("student", 0)
        self.simpan_page("course", 0)
        self.pool.unpin(self.pool.get_page("student", 0), True, 1)
        self.pool.unpin(self.pool.get_page("course", 0), True, 2)
        self.pool.discard_all()
        self.assertEqual(len(self.pool._frames), 0)
        self.assertEqual(self.disk.writes, [])
        self.assertEqual(self.disk.syncs, [])

    def test_discard_all_buffer_kosong_tidak_error(self):
        self.pool.discard_all()
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

    def page_dirty(self, lsn: int) -> Frame:
        self.simpan_page("student", 0)
        frame = self.pool.get_page("student", 0)
        self.pool.unpin(frame, True, lsn)
        return frame

    def test_callback_none_dianggap_berhasil(self):
        self.pool.set_log_flusher(lambda lsn: None)
        frame = self.page_dirty(3)
        self.pool.flush_page("student", 0)
        self.assertEqual(self.disk.writes, [("student", 0)])
        self.assertFalse(frame.dirty)

    def test_callback_false_membatalkan_tulis(self):
        self.pool.set_log_flusher(lambda lsn: False)
        frame = self.page_dirty(3)
        with self.assertRaises(StorageError):
            self.pool.flush_page("student", 0)
        self.assertEqual(self.disk.writes, [])
        self.assertTrue(frame.dirty)

    def test_callback_log_write_error_diteruskan(self):
        def gagal(lsn: int) -> bool:
            raise LogWriteError("log gagal ditulis")

        self.pool.set_log_flusher(gagal)
        frame = self.page_dirty(3)
        with self.assertRaises(LogWriteError):
            self.pool.flush_page("student", 0)
        self.assertEqual(self.disk.writes, [])
        self.assertTrue(frame.dirty)


class RusakDisk(FakeDisk):
    """FakeDisk yang bisa dipaksa melempar error mentah seperti DiskManager asli."""

    def __init__(self) -> None:
        super().__init__()
        self.errors: dict[str, Exception] = {}

    def _gagal(self, nama: str) -> None:
        if nama in self.errors:
            raise self.errors[nama]

    def read_page(self, table, page_id):
        self._gagal("read_page")
        return super().read_page(table, page_id)

    def write_page(self, table, page_id, data):
        self._gagal("write_page")
        super().write_page(table, page_id, data)

    def num_pages(self, table):
        self._gagal("num_pages")
        return super().num_pages(table)

    def allocate_page(self, table, data):
        self._gagal("allocate_page")
        return super().allocate_page(table, data)

    def sync(self, table=None):
        self._gagal("sync")
        super().sync(table)


class TestErrorDisk(PoolTestCase):
    """Error mentah dari DiskManager tidak boleh keluar sebagai exception Python biasa."""

    def setUp(self) -> None:
        super().setUp()
        self.disk = RusakDisk()
        self.disk.events = self.page.events
        self.pool = BufferPool(self.disk, capacity=2)

    def test_baca_gagal_menjadi_storage_error(self):
        for error in (KeyError("tabel tidak ada"), ValueError("ukuran"), OSError("io")):
            with self.subTest(error=type(error).__name__):
                self.disk.errors["read_page"] = error
                with self.assertRaises(StorageError):
                    self.pool.get_page("student", 0)
                self.assertEqual(len(self.pool._frames), 0)

    def test_alokasi_gagal_tidak_meninggalkan_frame(self):
        for nama in ("num_pages", "allocate_page"):
            with self.subTest(nama=nama):
                self.disk.errors = {nama: KeyError("disk tertutup")}
                with self.assertRaises(StorageError):
                    self.pool.new_page("student", 32)
                self.assertEqual(len(self.pool._frames), 0)

    def test_tulis_gagal_page_tetap_dirty(self):
        self.simpan_page("student", 0)
        frame = self.pool.get_page("student", 0)
        self.pool.unpin(frame, True, 2)
        self.disk.errors["write_page"] = OSError("disk penuh")
        with self.assertRaises(StorageError):
            self.pool.flush_page("student", 0)
        self.assertTrue(frame.dirty)
        self.assertIn(("student", 0), self.pool._frames)

    def test_tulis_gagal_saat_evict_tidak_memuat_page_baru(self):
        self.pool = BufferPool(self.disk, capacity=1)
        self.simpan_page("student", 0)
        self.simpan_page("student", 1)
        frame = self.pool.get_page("student", 0)
        self.pool.unpin(frame, True, 2)
        self.disk.errors["write_page"] = OSError("disk penuh")
        with self.assertRaises(StorageError):
            self.pool.get_page("student", 1)
        self.assertTrue(frame.dirty)
        self.assertEqual(list(self.pool._frames), [("student", 0)])
        self.disk.errors.clear()
        self.pool.get_page("student", 1)
        self.assertTrue(self.disk.pages[("student", 0)][-1] == 0xAA)

    def test_sync_gagal_menjadi_storage_error(self):
        aksi_flush = {
            "flush_all": lambda: self.pool.flush_all(),
            "flush_table": lambda: self.pool.flush_table("student"),
            "flush_page": lambda: self.pool.flush_page("student", 0),
        }
        for nama, aksi in aksi_flush.items():
            with self.subTest(aksi=nama):
                self.disk.errors.clear()
                self.simpan_page("student", 0)
                self.pool.unpin(self.pool.get_page("student", 0), True, 1)
                self.disk.errors["sync"] = OSError("fsync gagal")
                with self.assertRaises(StorageError):
                    aksi()


class TestKasusTepi(PoolTestCase):
    def test_pin_ulang_page_terpin_saat_penuh_tidak_evict(self):
        self.pool = BufferPool(self.disk, capacity=1)
        self.simpan_page("student", 0)
        first = self.pool.get_page("student", 0)
        second = self.pool.get_page("student", 0)
        self.assertIs(first, second)
        self.assertEqual(second.pin_count, 2)
        self.assertEqual(self.disk.reads, [("student", 0)])

    def test_unpin_bersih_tidak_menghapus_dirty(self):
        self.simpan_page("student", 0)
        frame = self.pool.get_page("student", 0)
        self.pool.get_page("student", 0)
        self.pool.unpin(frame, True, 3)
        self.pool.unpin(frame, False)
        self.assertTrue(frame.dirty)
        self.assertEqual(frame.page_lsn, 3)

    def test_nama_tabel_berbeda_page_id_sama_terpisah(self):
        self.simpan_page("student", 0)
        self.simpan_page("course", 0)
        a = self.pool.get_page("student", 0)
        b = self.pool.get_page("course", 0)
        self.assertIsNot(a, b)
        self.assertEqual(len(self.pool._frames), 2)

    def test_flush_log_dipanggil_per_page_sebelum_tiap_tulis(self):
        wal = FakeWal(self.disk.events)
        self.pool.set_log_flusher(wal.flush_log)
        self.simpan_page("student", 0)
        self.simpan_page("student", 1)
        self.pool.unpin(self.pool.get_page("student", 0), True, 3)
        self.pool.unpin(self.pool.get_page("student", 1), True, 7)
        self.pool.flush_all()
        self.assertEqual(wal.flushed, [3, 7])
        self.assertEqual(
            self.disk.events, ["wal", "seal", "write", "wal", "seal", "write"]
        )

    def test_flush_all_gagal_di_tengah_menyisakan_page_dirty(self):
        self.simpan_page("student", 0)
        self.simpan_page("student", 1)
        a = self.pool.get_page("student", 0)
        b = self.pool.get_page("student", 1)
        self.pool.unpin(a, True, 3)
        self.pool.unpin(b, True, 7)
        self.pool.set_log_flusher(lambda lsn: lsn < 7)
        with self.assertRaises(StorageError):
            self.pool.flush_all()
        self.assertFalse(a.dirty)
        self.assertTrue(b.dirty)
        self.assertEqual(self.disk.writes, [("student", 0)])
        self.assertEqual(self.disk.syncs, [])

    def test_page_bersih_tidak_memanggil_wal_maupun_menulis(self):
        wal = FakeWal()
        self.pool.set_log_flusher(wal.flush_log)
        self.simpan_page("student", 0)
        self.simpan_page("student", 1)
        self.simpan_page("student", 2)
        self.pool.unpin(self.pool.get_page("student", 0), False)
        self.pool.unpin(self.pool.get_page("student", 1), False)
        self.pool.flush_page("student", 0)
        self.pool.get_page("student", 2)
        self.assertEqual(wal.flushed, [])
        self.assertEqual(self.disk.writes, [])

    def test_dirty_tanpa_lsn_memanggil_wal_dengan_nol(self):
        wal = FakeWal()
        self.pool.set_log_flusher(wal.flush_log)
        self.simpan_page("student", 0)
        self.pool.unpin(self.pool.get_page("student", 0), True)
        self.pool.flush_all()
        self.assertEqual(wal.flushed, [0])

    def test_discard_lalu_baca_ulang_mengembalikan_isi_disk(self):
        self.simpan_page("student", 0)
        frame = self.pool.get_page("student", 0)
        frame.data[0] = 9
        self.pool.unpin(frame, True, 4)
        self.pool.discard_table("student")
        again = self.pool.get_page("student", 0)
        self.assertEqual(again.data[0], 0)
        self.assertEqual(self.disk.writes, [])

    def test_unpin_setelah_discard_ditolak(self):
        self.simpan_page("student", 0)
        frame = self.pool.get_page("student", 0)
        self.pool.discard_all()
        with self.assertRaises(StorageError):
            self.pool.unpin(frame, False)

    def test_new_page_setelah_discard_melanjutkan_penomoran(self):
        self.pool.unpin(self.pool.new_page("student", 32), False)
        self.pool.discard_all()
        frame = self.pool.new_page("student", 32)
        self.assertEqual(frame.page_id, 1)

    def test_new_page_saat_semua_terpin_tidak_mengalokasi_di_disk(self):
        self.pool.new_page("student", 32)
        self.pool.new_page("student", 32)
        with self.assertRaisesRegex(StorageError, "buffer penuh"):
            self.pool.new_page("student", 32)
        self.assertEqual(len(self.disk.allocations), 2)

    def test_kapasitas_satu_bergantian_tanpa_kehilangan_data(self):
        self.pool = BufferPool(self.disk, capacity=1)
        for page_id in range(3):
            frame = self.pool.new_page("student", 32)
            frame.data[1] = page_id + 1
            self.pool.unpin(frame, True, page_id + 1)
        self.pool.flush_all()
        for page_id in range(3):
            self.assertEqual(self.disk.pages[("student", page_id)][1], page_id + 1)


class TestKonkurensi(PoolTestCase):
    def test_dua_thread_pin_unpin_tidak_merusak_hitungan(self):
        pool = BufferPool(self.disk, capacity=3)
        wal = FakeWal()
        pool.set_log_flusher(wal.flush_log)
        for page_id in range(4):
            self.simpan_page("student", page_id)
        galat: list[Exception] = []

        def kerja(offset: int) -> None:
            try:
                for i in range(300):
                    frame = pool.get_page("student", (i + offset) % 4)
                    pool.unpin(frame, i % 2 == 0, i + 1)
            except Exception as exc:  # noqa: BLE001
                galat.append(exc)

        threads = [threading.Thread(target=kerja, args=(n,)) for n in (0, 2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(galat, [])
        self.assertLessEqual(len(pool._frames), 3)
        self.assertTrue(all(f.pin_count == 0 for f in pool._frames.values()))


if __name__ == "__main__":
    unittest.main()
