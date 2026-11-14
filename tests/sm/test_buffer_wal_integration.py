"""Integrasi SM + FRM: hook WAL buffer pool memakai DiskManager, SlottedPage, dan FRM asli (SM-12, SM-13)."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from common.catalog import ColumnSchema, DataType
from common.errors import StorageError
from common.log import LogKind, LogRecord
from failure_recovery import FailureRecoveryManager
from failure_recovery.log import codec
from storage_manager.facade import StorageManager
from storage_manager.page import SlottedPage

TABLE = "student"
MAX_BARIS = 3000


class WalIntegrationTestCase(unittest.TestCase):
    """Satu SM (buffer kecil) dan satu FRM asli di folder sementara."""

    frames = 2

    def setUp(self) -> None:
        previous_path = codec.get_log_path()
        self.addCleanup(codec.set_log_path, previous_path)
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.data_dir = temp.name
        self.sm = self.buka_sm()
        self.sm._catalog.create_table(
            TABLE,
            (
                ColumnSchema("id", DataType.INTEGER, nullable=False),
                ColumnSchema("name", DataType.VARCHAR, length=50),
            ),
            ("id",),
            (),
        )
        self.frm = FailureRecoveryManager(log_path=str(Path(self.data_dir) / "wal.log"))
        self.addCleanup(self.frm.close)
        self.sm.set_log_flusher(self.frm.flush_log)
        self.penulisan: list[tuple[int, int, int]] = []

    def buka_sm(self) -> StorageManager:
        sm = StorageManager(data_dir=self.data_dir, buffer_frames=self.frames)
        self.addCleanup(sm._disk.close)
        return sm

    def pantau_tulis_disk(self) -> None:
        """Catat (page_id, page_lsn di header, LSN log yang sudah permanen) tiap penulisan page."""
        asli = self.sm._disk.write_page

        def pantau(table: str, page_id: int, data: bytes) -> None:
            header = SlottedPage.read_header(bytearray(data))
            flushed = self.frm._log_writer._flushed_lsn
            self.penulisan.append((page_id, header.page_lsn, flushed))
            asli(table, page_id, data)

        self.sm._disk.write_page = pantau

    def tambah(self, nomor: int) -> int:
        """Satu INSERT dua langkah dengan LSN asli dari log FRM. Mengembalikan LSN."""
        lsn = self.frm.write_log(LogRecord(LogKind.BEGIN, tx_id=nomor)).lsn
        rid = self.sm.allocate_rid(TABLE)
        self.sm.insert(TABLE, rid, [nomor, f"mhs{nomor}"], lsn)
        return lsn

    def isi_sampai(self, minimal_page: int) -> int:
        """Tambah baris sampai tabel punya cukup page. Mengembalikan jumlah baris."""
        nomor = 0
        while self.sm._disk.num_pages(TABLE) < minimal_page:
            nomor += 1
            self.assertLess(nomor, MAX_BARIS, "tabel tidak pernah mencapai jumlah page target")
            self.tambah(nomor)
        return nomor


class TestHookWalDenganFrmAsli(WalIntegrationTestCase):
    def test_log_permanen_sebelum_page_tertulis_saat_penggantian(self):
        self.pantau_tulis_disk()
        self.isi_sampai(4)
        self.assertTrue(self.penulisan, "buffer kecil harus memicu penggantian page dirty")
        self.assertGreater(max(lsn for _, lsn, _ in self.penulisan), 0)
        for page_id, page_lsn, flushed in self.penulisan:
            with self.subTest(page_id=page_id):
                self.assertGreaterEqual(flushed, page_lsn)

    def test_log_permanen_sebelum_page_tertulis_saat_flush_all(self):
        self.pantau_tulis_disk()
        for nomor in range(1, 6):
            self.tambah(nomor)
        self.assertEqual(self.penulisan, [])
        self.sm._buffer.flush_all()
        self.assertTrue(self.penulisan)
        self.assertGreater(max(lsn for _, lsn, _ in self.penulisan), 0)
        for page_id, page_lsn, flushed in self.penulisan:
            with self.subTest(page_id=page_id):
                self.assertGreaterEqual(flushed, page_lsn)

    def test_flush_facade_seperti_checkpoint_frm_menulis_setelah_log_permanen(self):
        self.pantau_tulis_disk()
        for nomor in range(1, 6):
            self.tambah(nomor)
        self.sm.flush()
        self.assertTrue(self.penulisan)
        self.assertGreater(max(lsn for _, lsn, _ in self.penulisan), 0)
        for page_id, page_lsn, flushed in self.penulisan:
            with self.subTest(page_id=page_id):
                self.assertGreaterEqual(flushed, page_lsn)
        self.assertFalse(any(f.dirty for f in self.sm._buffer._frames.values()))

    def test_flush_facade_gagal_saat_frm_tertutup_dan_page_tetap_dirty(self):
        self.tambah(1)
        self.pantau_tulis_disk()
        self.frm.close()
        with self.assertRaises(StorageError):
            self.sm.flush()
        self.assertEqual(self.penulisan, [])
        self.assertTrue(any(f.dirty for f in self.sm._buffer._frames.values()))

    def test_frm_gagal_flush_all_tidak_menulis_dan_page_tetap_dirty(self):
        self.tambah(1)
        self.pantau_tulis_disk()
        self.frm.close()
        with self.assertRaises(StorageError):
            self.sm._buffer.flush_all()
        self.assertEqual(self.penulisan, [])
        self.assertTrue(any(f.dirty for f in self.sm._buffer._frames.values()))

    def test_frm_gagal_saat_penggantian_membatalkan_dan_data_tidak_hilang(self):
        self.isi_sampai(2)
        self.tambah(MAX_BARIS)
        jumlah_sebelum = len(list(self.sm.scan(TABLE)))
        halaman_sebelum = self.sm._disk.num_pages(TABLE)
        self.pantau_tulis_disk()
        self.frm.close()
        # semua frame penuh dan dirty, jadi page baru memaksa penggantian page dirty
        with self.assertRaises(StorageError):
            self.sm._buffer.new_page(TABLE, 64)
        self.assertEqual(self.penulisan, [])
        self.assertEqual(self.sm._disk.num_pages(TABLE), halaman_sebelum)
        self.assertEqual(len(self.sm._buffer._frames), self.frames)
        self.assertEqual(len(list(self.sm.scan(TABLE))), jumlah_sebelum)


class TestDataBertahanSetelahRestart(WalIntegrationTestCase):
    def test_data_dan_checksum_utuh_setelah_flush_dan_buka_ulang(self):
        jumlah = self.isi_sampai(4)
        self.sm._buffer.flush_all()
        self.sm._disk.close()

        sm2 = self.buka_sm()
        baris = list(sm2.scan(TABLE))
        self.assertEqual(len(baris), jumlah)
        self.assertEqual(sorted(r.values[0] for r in baris), list(range(1, jumlah + 1)))
        for page_id in range(sm2._disk.num_pages(TABLE)):
            with self.subTest(page_id=page_id):
                self.assertTrue(SlottedPage.verify(sm2._disk.read_page(TABLE, page_id)))

    def test_page_yang_tidak_berubah_tidak_ditulis_ulang(self):
        self.isi_sampai(4)
        self.sm._buffer.flush_all()
        self.pantau_tulis_disk()
        self.sm._buffer.flush_all()
        self.assertEqual(self.penulisan, [])

    def test_page_di_buffer_dibaca_ulang_tanpa_menyentuh_disk(self):
        self.tambah(1)
        pembacaan: list[int] = []
        asli = self.sm._disk.read_page

        def pantau(table: str, page_id: int):
            pembacaan.append(page_id)
            return asli(table, page_id)

        self.sm._disk.read_page = pantau
        self.assertEqual(len(list(self.sm.scan(TABLE))), 1)
        self.assertEqual(len(list(self.sm.scan(TABLE))), 1)
        self.assertEqual(pembacaan, [])


if __name__ == "__main__":
    unittest.main()
