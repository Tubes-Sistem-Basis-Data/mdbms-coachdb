from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum

from common.transaction import AccessResponse, AccessStatus, Action, ResourceId
from concurrency_control.deadlock import DeadlockDetector
from concurrency_control.protocol import ConcurrencyProtocol, Transaction


class LockMode(Enum):
    """Mode lock multiple granularity. Tipe internal CCM."""

    IS = "IS"
    IX = "IX"
    S = "S"
    SIX = "SIX"
    X = "X"


_COMPATIBLE: dict[LockMode, set[LockMode]] = {
    LockMode.IS: {LockMode.IS, LockMode.IX, LockMode.S, LockMode.SIX},
    LockMode.IX: {LockMode.IS, LockMode.IX},
    LockMode.S: {LockMode.IS, LockMode.S},
    LockMode.SIX: {LockMode.IS},
    LockMode.X: set(),
}

_COVERS: dict[LockMode, set[LockMode]] = {
    LockMode.IS: {LockMode.IS},
    LockMode.IX: {LockMode.IS, LockMode.IX},
    LockMode.S: {LockMode.IS, LockMode.S},
    LockMode.SIX: {LockMode.IS, LockMode.IX, LockMode.S, LockMode.SIX},
    LockMode.X: set(LockMode),
}

_STRENGTH: list[LockMode] = [LockMode.IS, LockMode.IX, LockMode.S, LockMode.SIX, LockMode.X]


def compatible(a: LockMode, b: LockMode) -> bool:
    """True jika mode `a` dan `b` boleh dipegang dua transaksi berbeda sekaligus."""
    return b in _COMPATIBLE[a]


def covers(held: LockMode, wanted: LockMode) -> bool:
    """True jika lock `held` sudah memberi hak `wanted`."""
    return wanted in _COVERS[held]


def combine(a: LockMode, b: LockMode) -> LockMode:
    """Mode terlemah yang mencakup `a` dan `b` (misal S + IX = SIX)."""
    for mode in _STRENGTH:
        if covers(mode, a) and covers(mode, b):
            return mode
    return LockMode.X


@dataclass
class _Waiter:
    tx_id: int
    mode: LockMode
    upgrade: bool


@dataclass
class _LockEntry:
    granted: dict[int, LockMode] = field(default_factory=dict)
    queue: list[_Waiter] = field(default_factory=list)


class LockManager(ConcurrencyProtocol):
    """Protokol Strict 2PL multiple granularity dengan deteksi deadlock wait-for graph."""

    def __init__(self, detector: DeadlockDetector | None = None) -> None:
        self._mutex = threading.Lock()
        self._locks: dict[ResourceId, _LockEntry] = {}
        # tx -> resource yang dikunci, biar on_finish ga perlu scan semua lock
        self._held: dict[int, set[ResourceId]] = {}
        # tx -> resource yang lagi ditunggu (satu tx cuma nunggu satu resource sekaligus)
        self._waiting: dict[int, ResourceId] = {}
        self._victims: set[int] = set()
        self._detector = detector if detector is not None else DeadlockDetector()

    def on_begin(self, tx: Transaction) -> None:
        """Tidak ada yang perlu disiapkan; lock dicatat saat pertama diminta."""

    def request(self, tx: Transaction, resource: ResourceId, action: Action) -> AccessResponse:
        """Minta lock untuk akses `action` ke `resource`, termasuk intention lock di tabel.

        Mengembalikan ALLOWED, WAIT (posisi antrean disimpan), atau ABORTED kalau tx jadi
        victim deadlock. Abort-nya sendiri dijalankan facade.
        """
        with self._mutex:
            tx_id = tx.tx_id
            if tx_id in self._victims:
                return self._aborted(tx_id)

            for res, mode in self._plan(tx_id, resource, action):
                status, blockers = self._acquire(tx_id, res, mode)
                if status is AccessStatus.ALLOWED:
                    continue
                victim = self._detector.on_wait(tx_id, blockers)
                if victim is not None:
                    self._victims.add(victim)
                    if victim == tx_id:
                        return self._aborted(tx_id)
                return AccessResponse(
                    AccessStatus.WAIT,
                    message=f"T{tx_id} menunggu lock {mode.value} pada {_describe(res)}",
                )

            self._detector.on_granted(tx_id)
            return AccessResponse(AccessStatus.ALLOWED)

    def on_finish(self, tx_id: int) -> None:
        """Lepas semua lock dan antrean tx (commit/abort selesai), lalu perbarui wait-for graph."""
        with self._mutex:
            touched = self._held.pop(tx_id, set())
            for res in touched:
                entry = self._locks.get(res)
                if entry is not None:
                    entry.granted.pop(tx_id, None)
            waited = self._waiting.pop(tx_id, None)
            if waited is not None:
                touched.add(waited)
                entry = self._locks.get(waited)
                if entry is not None:
                    entry.queue = [w for w in entry.queue if w.tx_id != tx_id]
            for res in touched:
                entry = self._locks.get(res)
                if entry is not None and not entry.granted and not entry.queue:
                    del self._locks[res]
            self._victims.discard(tx_id)
            self._detector.on_finished(tx_id)
            self._refresh_waits()

    def held_locks(self, tx_id: int) -> dict[ResourceId, LockMode]:
        """Lock yang sedang dipegang tx. Untuk debug dan pengujian."""
        with self._mutex:
            return {res: self._locks[res].granted[tx_id] for res in self._held.get(tx_id, ())}

    def _plan(self, tx_id: int, resource: ResourceId, action: Action) -> list[tuple[ResourceId, LockMode]]:
        # Urutan lock dari atas ke bawah: intention di tabel dulu, baru baris
        leaf_mode = LockMode.S if action is Action.READ else LockMode.X
        if resource.rid is None:
            return [(resource, leaf_mode)]
        table = ResourceId(resource.table)
        table_held = self._mode_of(tx_id, table)
        # Lock tabel yang udah cukup kuat (S buat baca, X buat tulis) bikin lock baris ga perlu
        if table_held is not None and covers(table_held, leaf_mode):
            return [(table, leaf_mode)]
        intention = LockMode.IS if action is Action.READ else LockMode.IX
        return [(table, intention), (resource, leaf_mode)]

    def _acquire(self, tx_id: int, res: ResourceId, wanted: LockMode) -> tuple[AccessStatus, set[int]]:
        # Return (ALLOWED, kosong) kalau dapat lock, atau (WAIT, penghalang) kalau harus antre
        entry = self._locks.setdefault(res, _LockEntry())
        held = entry.granted.get(tx_id)
        if held is not None and covers(held, wanted):
            self._dequeue(tx_id, res, entry)
            return AccessStatus.ALLOWED, set()

        target = wanted if held is None else combine(held, wanted)
        upgrade = held is not None
        waiter = self._enqueue(tx_id, res, entry, target, upgrade)
        blockers = self._blockers(entry, waiter)
        if blockers:
            return AccessStatus.WAIT, blockers

        self._dequeue(tx_id, res, entry)
        entry.granted[tx_id] = target
        self._held.setdefault(tx_id, set()).add(res)
        return AccessStatus.ALLOWED, set()

    def _blockers(self, entry: _LockEntry, waiter: _Waiter) -> set[int]:
        # Penghalang = pemegang lock yang ga kompatibel + antrean di depan yang ga kompatibel (FIFO)
        blockers = {
            other for other, mode in entry.granted.items()
            if other != waiter.tx_id and not compatible(mode, waiter.mode)
        }
        for ahead in entry.queue:
            if ahead is waiter:
                break
            if not compatible(ahead.mode, waiter.mode):
                blockers.add(ahead.tx_id)
        return blockers

    def _enqueue(self, tx_id: int, res: ResourceId, entry: _LockEntry, mode: LockMode, upgrade: bool) -> _Waiter:
        # Retry pakai posisi antrean yang sama. upgrade ditaruh di depan antrean biasa
        for waiter in entry.queue:
            if waiter.tx_id == tx_id:
                waiter.mode = mode
                return waiter
        old = self._waiting.get(tx_id)
        if old is not None and old != res:
            # Request beda dari yang lagi ditunggu, posisi lama dibuang
            self._dequeue(tx_id, old, self._locks[old])
        waiter = _Waiter(tx_id, mode, upgrade)
        if upgrade:
            pos = next((i for i, w in enumerate(entry.queue) if not w.upgrade), len(entry.queue))
            entry.queue.insert(pos, waiter)
        else:
            entry.queue.append(waiter)
        self._waiting[tx_id] = res
        return waiter

    def _dequeue(self, tx_id: int, res: ResourceId, entry: _LockEntry) -> None:
        if self._waiting.get(tx_id) == res:
            del self._waiting[tx_id]
            entry.queue = [w for w in entry.queue if w.tx_id != tx_id]

    def _refresh_waits(self) -> None:
        # Pemegang lock/antrean berubah, jadi penghalang semua yang lagi nunggu dihitung ulang
        # Cycle-nya tetap dicek pas retry lewat on_wait, di sini cuma update sisi graf
        for tx_id, res in self._waiting.items():
            entry = self._locks[res]
            waiter = next(w for w in entry.queue if w.tx_id == tx_id)
            self._detector.graph.set_waits(tx_id, self._blockers(entry, waiter))

    def _mode_of(self, tx_id: int, res: ResourceId) -> LockMode | None:
        entry = self._locks.get(res)
        return None if entry is None else entry.granted.get(tx_id)

    @staticmethod
    def _aborted(tx_id: int) -> AccessResponse:
        return AccessResponse(AccessStatus.ABORTED, message=f"T{tx_id} dibatalkan karena deadlock")


def _describe(res: ResourceId) -> str:
    if res.rid is None:
        return f"tabel {res.table}"
    return f"{res.table}({res.rid.page_id}, {res.rid.slot})"
