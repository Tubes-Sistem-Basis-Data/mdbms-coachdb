# CC-07: masih skeleton. belum detect deadlock tapi intinya perlu cc2 (nanti buat masukin penghalang dari lock and antrian) dan ccm/cc3 handle abort victim dan rollback sebelum release (perlu koordinasi perubahan graf sama mutex cc2)

from __future__ import annotations


class WaitForGraph:
    # Graf dari yang nunggu -> yang ngalangin (ini terpisah dari precedence graf cc4)

    def __init__(self) -> None:
        self.edges: dict[int, set[int]] = {}

    def set_waits(self, waiter: int, holders: set[int]) -> None:
        # Ganti sisi keluar waiter pakai penghalang dari cc2 (yang megang lock, antrian, sama upgrade lock)
        # Kalau holders kosong, hapus sisi keluar aja. sisi masuk dari transaksi lain tetap ada
        raise NotImplementedError

    def remove(self, tx: int) -> None:
        # Hapus simpul tx sama semua sisi masuk dan keluarnya
        raise NotImplementedError

    def find_cycle(self, start: int) -> list[int] | None:
        # Cari cycle yang bisa reached dari start pake DFS, nanti return anggota cycle atau None
        # Kalo cuma nunggu tapi gaada cycle, jangan anggep as deadlock
        raise NotImplementedError


class DeadlockDetector:

    def __init__(self) -> None:
        self.graph = WaitForGraph()

    def on_wait(self, waiter: int, holders: set[int]) -> int | None:
        # Tiap request ketahan, update yang ngalangin terus cek cycle
        # Return tx_id victim dari cycle atau None kalau gaada
        # cc2 simpen victim-nya: kalau victim = yang lagi request, balikin ABORTED sekarang
        # kalau victim tx lain, dia pasti lagi WAIT jadi bakal retry, balikin ABORTED pas retry itu
        # facade (cc1) yang jalanin abort setelah dapet ABORTED, jadi detector ga manggil abort
        raise NotImplementedError

    def choose_victim(self, cycle: list[int]) -> int:
        # Ambil tx_id paling gede dari cycle yang ga kosong (paling muda karena id ngikut urutan mulai)
        # Yang di luar cycle jangan ikut dipilih walaupun idnya lebih gede
        raise NotImplementedError

    def on_finished(self, tx: int) -> None:
        # Kalo tx selesai, bersihin sisi masuk sama keluarnya
        # cc2 juga perlu update penghalang transaksi lain kalau yang megang lock atau posisi antri berubah
        raise NotImplementedError

    def on_granted(self, tx: int) -> None:
        # Hapus sisi keluar tx aja. transaksi lain yang masih nunggu tx jangan ikut dihapus
        raise NotImplementedError
