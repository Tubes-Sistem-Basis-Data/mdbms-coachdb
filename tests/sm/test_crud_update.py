"""Kerangka uji SM-06: mengubah data (update)."""

from __future__ import annotations

import unittest


@unittest.skip("SM-06: aktifkan setelah update diimplementasikan")
class TestUpdate(unittest.TestCase):
    """Skenario RecordManager.update()."""

    def test_rid_tidak_berubah(self) -> None:
        """Update menulis di slot yang sama sehingga RID tetap."""

    def test_update_idempoten(self) -> None:
        """Dua kali update dengan argumen sama menghasilkan state sama."""

    def test_index_diperbarui_saat_kolom_terindex_berubah(self) -> None:
        """on_update dipanggil ketika nilai kolom ter-index berubah."""

    def test_index_tidak_disentuh_saat_kolom_biasa_berubah(self) -> None:
        """Kolom tanpa index tidak memicu on_update."""

    def test_statistik_di_invalidate(self) -> None:
        """Statistik tabel di-invalidate setelah update."""

    def test_primary_key_ganda_mengecualikan_record_sendiri(self) -> None:
        """Cek PK saat update tidak menabrak RID yang sedang diubah."""

    def test_slot_bukan_live(self) -> None:
        """Perilaku update pada slot tombstone masih menunggu keputusan tim."""


if __name__ == "__main__":
    unittest.main()
