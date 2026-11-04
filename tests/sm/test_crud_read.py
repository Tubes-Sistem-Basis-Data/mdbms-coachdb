"""Kerangka uji SM-04: mengambil data tabel (scan dan fetch)."""

from __future__ import annotations

import unittest


@unittest.skip("SM-04: aktifkan setelah scan diimplementasikan")
class TestScan(unittest.TestCase):
    """Skenario RecordManager.scan()."""

    def test_tabel_kosong_tidak_menghasilkan_baris(self) -> None:
        """Tabel tanpa record menghasilkan iterator kosong."""

    def test_urutan_hasil_page_dan_slot(self) -> None:
        """Baris keluar urut (page_id, slot)."""

    def test_tombstone_tidak_muncul(self) -> None:
        """Slot yang sudah dihapus tidak ikut di-yield."""

    def test_tabel_tidak_ada_raise_table_not_found(self) -> None:
        """Nama tabel asing memunculkan TableNotFoundError."""

    def test_pin_count_kembali_nol(self) -> None:
        """Page di-unpin sebelum yield dan pin count akhir nol."""


@unittest.skip("SM-04: aktifkan setelah fetch diimplementasikan")
class TestFetch(unittest.TestCase):
    """Skenario RecordManager.fetch()."""

    def test_rid_valid_mengembalikan_baris(self) -> None:
        """RID pada slot LIVE mengembalikan Row yang benar."""

    def test_slot_kosong_mengembalikan_none(self) -> None:
        """Slot EMPTY menghasilkan None."""

    def test_tombstone_mengembalikan_none(self) -> None:
        """Slot yang sudah dihapus menghasilkan None."""

    def test_page_tidak_ada_mengembalikan_none(self) -> None:
        """RID dengan page_id di luar jangkauan menghasilkan None."""

    def test_tabel_tidak_ada_raise_table_not_found(self) -> None:
        """Nama tabel asing memunculkan TableNotFoundError."""


if __name__ == "__main__":
    unittest.main()
