# CC-07: deteksi deadlock pakai wait-for graph. penghalang diisi cc2 (lock_manager) dari yang megang lock, antrian, sama upgrade lock
# Abort victim sama rollback tetap dijalanin facade (cc1/cc3), detector cuma milih victim. semua method dipanggil dengan mutex cc2 dipegang

from __future__ import annotations


class WaitForGraph:
    # Graf dari yang nunggu -> yang ngalangin (ini terpisah dari precedence graf cc4)

    def __init__(self) -> None:
        self.edges: dict[int, set[int]] = {}

    def set_waits(self, waiter: int, holders: set[int]) -> None:
        # Ganti sisi keluar waiter pakai penghalang dari cc2 (yang megang lock, antrian, sama upgrade lock)
        # Kalau holders kosong, hapus sisi keluar aja. sisi masuk dari transaksi lain tetap ada
        targets = set(holders) - {waiter}
        if targets:
            self.edges[waiter] = targets
        else:
            self.edges.pop(waiter, None)

    def remove(self, tx: int) -> None:
        # Hapus simpul tx sama semua sisi masuk dan keluarnya
        self.edges.pop(tx, None)
        for waiter in list(self.edges):
            targets = self.edges[waiter]
            targets.discard(tx)
            if not targets:
                del self.edges[waiter]

    def find_cycle(self, start: int) -> list[int] | None:
        # Cari cycle yang bisa reached dari start pake DFS, return anggota cycle atau None
        # Kalo cuma nunggu tapi gaada cycle, jangan anggep as deadlock
        path: list[int] = []
        on_path: set[int] = set()
        done: set[int] = set()
        stack: list[tuple[int, list[int]]] = []

        def push(node: int) -> None:
            path.append(node)
            on_path.add(node)
            stack.append((node, sorted(self.edges.get(node, ()))))

        push(start)
        while stack:
            node, neighbors = stack[-1]
            if not neighbors:
                stack.pop()
                path.pop()
                on_path.discard(node)
                done.add(node)
                continue
            nxt = neighbors.pop(0)
            if nxt in on_path:
                return path[path.index(nxt):]
            if nxt not in done:
                push(nxt)
        return None


class DeadlockDetector:

    def __init__(self) -> None:
        self.graph = WaitForGraph()

    def on_wait(self, waiter: int, holders: set[int]) -> int | None:
        # Tiap request ketahan, update yang ngalangin terus cek cycle
        self.graph.set_waits(waiter, holders)
        cycle = self.graph.find_cycle(waiter)
        if cycle is None:
            return None
        return self.choose_victim(cycle)

    def choose_victim(self, cycle: list[int]) -> int:
        # Ambil tx_id paling gede dari cycle yang ga kosong (paling muda karena id ngikut urutan mulai)
        # Yang di luar cycle jangan ikut dipilih walaupun idnya lebih gede
        if not cycle:
            raise ValueError("cycle kosong, tidak ada victim")
        return max(cycle)

    def on_finished(self, tx: int) -> None:
        # Kalo tx selesai, bersihin sisi masuk sama keluarnya
        # cc2 juga perlu update penghalang transaksi lain kalau yang megang lock atau posisi antri berubah
        self.graph.remove(tx)

    def on_granted(self, tx: int) -> None:
        # Hapus sisi keluar tx aja. transaksi lain yang masih nunggu tx jangan ikut dihapus
        self.graph.set_waits(tx, set())
