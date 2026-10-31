"""Mock Concurrency Control Manager: semua akses diizinkan."""

from __future__ import annotations

import threading

from common.errors import MdbmsError
from common.interfaces import ConcurrencyControlManager, IFailureRecovery
from common.transaction import (
    AccessResponse,
    AccessStatus,
    Action,
    DataChange,
    ProtocolType,
    ResourceId,
    RollbackResult,
    TxResult,
    TxStatus,
)


class MockConcurrencyControlManager(ConcurrencyControlManager):
    """CCM tanpa lock. Tetap menulis log ke FRM dan mengikuti alur commit/abort."""

    def __init__(self, frm: IFailureRecovery) -> None:
        self._frm = frm
        self._lock = threading.Lock()
        self._idle = threading.Condition(self._lock)
        self._last_tx_id = 0
        self._active: set[int] = set()
        self._paused = False
        self._protocol = ProtocolType.LOCK_BASED
        # (tx_id, resource, repr(change)) -> lsn, supaya permintaan ulang tidak menulis log ganda
        self._written: dict[tuple[int, ResourceId, str], int] = {}

    def begin_transaction(self) -> int:
        """Beri tx_id berikutnya dan catat BEGIN. Blok selama transaksi baru ditahan."""
        with self._idle:
            while self._paused:
                self._idle.wait()
            self._last_tx_id += 1
            tx_id = self._last_tx_id
            self._active.add(tx_id)
        self._frm.log_begin(tx_id)
        return tx_id

    def validate_object(
        self,
        tx_id: int,
        resource: ResourceId,
        action: Action,
        change: DataChange | None = None,
    ) -> AccessResponse:
        """Selalu ALLOWED untuk transaksi aktif. WRITE menghasilkan satu log perubahan."""
        with self._lock:
            if tx_id not in self._active:
                return AccessResponse(AccessStatus.ABORTED, message=f"transaksi {tx_id} tidak aktif")
        if action is Action.READ:
            return AccessResponse(AccessStatus.ALLOWED)
        if change is None:
            return AccessResponse(AccessStatus.ABORTED, message="WRITE tanpa DataChange")
        key = (tx_id, resource, repr(change))
        with self._lock:
            if key in self._written:
                return AccessResponse(AccessStatus.ALLOWED, lsn=self._written[key])
        lsn = self._frm.log_write(
            tx_id,
            change.op,
            change.rid,
            change.before,
            change.after,
        )
        with self._lock:
            self._written[key] = lsn
        return AccessResponse(AccessStatus.ALLOWED, lsn=lsn)

    def _finish(self, tx_id: int) -> None:
        with self._idle:
            self._active.discard(tx_id)
            self._written = {k: v for k, v in self._written.items() if k[0] != tx_id}
            self._idle.notify_all()

    def commit(self, tx_id: int) -> TxResult:
        """Catat COMMIT, flush log, lalu tutup transaksi."""
        with self._lock:
            if tx_id not in self._active:
                return TxResult(tx_id, TxStatus.FAILED, ok=False, message="transaksi tidak aktif")
        lsn = self._frm.log_commit(tx_id)
        self._frm.flush_log_until(lsn)
        self._finish(tx_id)
        return TxResult(tx_id, TxStatus.COMMITTED, ok=True)

    def abort(self, tx_id: int) -> TxResult:
        """Rollback lewat FRM (ABORT_REQUEST, undo, ABORT_END), lalu tutup transaksi."""
        with self._lock:
            if tx_id not in self._active:
                return TxResult(tx_id, TxStatus.FAILED, ok=False, message="transaksi tidak aktif")
        result = self._frm.rollback(tx_id)
        if result not in (RollbackResult.DONE, RollbackResult.ALREADY_ABORT):
            return TxResult(tx_id, TxStatus.FAILED, ok=False, message=str(result))
        self._finish(tx_id)
        return TxResult(tx_id, TxStatus.ABORTED, ok=True)

    def set_protocol(self, protocol: ProtocolType) -> None:
        """Ganti protokol. Raise MdbmsError jika masih ada transaksi aktif."""
        with self._lock:
            if self._active:
                raise MdbmsError("protokol tidak bisa diganti saat ada transaksi aktif")
            self._protocol = protocol

    def get_protocol(self) -> ProtocolType:
        """Protokol aktif."""
        return self._protocol

    def initialize(self, last_tx_id: int) -> None:
        """tx_id berikutnya dimulai dari last_tx_id + 1."""
        with self._lock:
            self._last_tx_id = last_tx_id

    def pause_new_transactions(self) -> None:
        """Tahan begin_transaction berikutnya."""
        with self._lock:
            self._paused = True

    def wait_until_idle(self) -> None:
        """Blok sampai tidak ada transaksi aktif."""
        with self._idle:
            while self._active:
                self._idle.wait()

    def resume_transactions(self) -> None:
        """Lepas penahanan transaksi baru."""
        with self._idle:
            self._paused = False
            self._idle.notify_all()

    """ Mocks spesifik untuk FRM """

    def hold_new_transactions(self) -> None:
        """Alias pause_new_transactions."""
        self.pause_new_transactions()

    def wait_until_no_active(self, timeout: float) -> None:
        """Blok sampai idle atau timeout."""
        with self._idle:
            self._idle.wait_for(lambda: not self._active, timeout=timeout)

    def release_hold(self) -> None:
        """Alias resume_transactions."""
        self.resume_transactions()
