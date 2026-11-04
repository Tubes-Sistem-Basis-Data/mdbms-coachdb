"""Kerangka uji SM-05: menambah data (insert, allocate_rid, release_rid)."""

from __future__ import annotations

import unittest


@unittest.skip("SM-05: aktifkan setelah insert diimplementasikan")
class TestInsert(unittest.TestCase):
    """Skenario RecordManager.insert()."""

    def test_rid_berurutan(self) -> None:
        """RID hasil insert mengikuti urutan (page_id, slot)."""

    def test_page_baru_saat_page_penuh(self) -> None:
        """Insert berikutnya memakai page baru ketika page terakhir penuh."""

    def test_log_fn_dipanggil_sekali_dengan_rid_benar(self) -> None:
        """log_fn menerima RID yang sama dengan RID hasil insert."""

    def test_log_fn_gagal_tidak_meninggalkan_record(self) -> None:
        """Jika log_fn raise, tidak ada slot yang tertulis dan pin dilepas."""

    def test_nilai_invalid_ditolak_tanpa_memanggil_log_fn(self) -> None:
        """Validasi berjalan sebelum log_fn dipanggil."""

    def test_primary_key_ganda_raise_schema_violation(self) -> None:
        """PK duplikat memunculkan SchemaViolationError."""

    def test_tombstone_tidak_dipakai_ulang(self) -> None:
        """Slot yang sudah dihapus tidak dialokasikan untuk insert baru."""


@unittest.skip("SM-05: aktifkan setelah allocate_rid dan release_rid diimplementasikan")
class TestRidAllocation(unittest.TestCase):
    """Skenario RecordManager.allocate_rid() dan release_rid()."""

    def test_allocate_rid_unik(self) -> None:
        """Dua alokasi berturut-turut tidak menghasilkan RID yang sama."""

    def test_release_rid_membatalkan_alokasi(self) -> None:
        """RID yang dilepas boleh dipakai lagi oleh alokasi berikutnya."""


@unittest.skip("SM-05: aktifkan setelah insert_at diimplementasikan")
class TestInsertAt(unittest.TestCase):
    """Skenario RecordManager.insert_at()."""

    def test_insert_at_idempoten(self) -> None:
        """Dua kali insert_at dengan argumen sama menghasilkan state sama."""

    def test_insert_at_menghidupkan_tombstone(self) -> None:
        """insert_at pada slot tombstone mengembalikan slot menjadi LIVE."""


if __name__ == "__main__":
    unittest.main()
