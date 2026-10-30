"""Mock Concurrency Control Manager: semua akses diizinkan."""

from __future__ import annotations

import threading

from common.errors import MdbmsError
from common.interfaces import ConcurrencyControlManager, FailureRecoveryManager
from common.log import LogKind, LogRecord
from common.transaction import (
    AccessResponse,
    AccessStatus,
    Action,
    DataChange,
    ProtocolType,
    ResourceId,
    TxResult,
    TxStatus,
)


class MockConcurrencyControlManager(ConcurrencyControlManager):
    """CCM tanpa lock. Tetap menulis log ke FRM dan mengikuti alur commit/abort."""

    def __init__(self, frm: FailureRecoveryManager) -> None:
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
        self._frm.write_log(LogRecord(LogKind.BEGIN, tx_id=tx_id))
        return tx_id

    def validate_object(
        self,
        tx_id: int,
        resource: ResourceId,
        action: Action,
        change: DataChange | None = None,
    ) -> AccessResponse:
        """Selalu ALLOWED untuk transaksi aktif. WRITE menghasilkan satu log DATA."""
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
        res = self._frm.write_log(LogRecord(LogKind.DATA, tx_id=tx_id, change=change))
        if not res.ok:
            return AccessResponse(AccessStatus.ABORTED, message=res.message)
        with self._lock:
            self._written[key] = res.lsn
        return AccessResponse(AccessStatus.ALLOWED, lsn=res.lsn)

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
        res = self._frm.write_log(LogRecord(LogKind.COMMIT, tx_id=tx_id))
        self._frm.flush_log(res.lsn)
        self._finish(tx_id)
        return TxResult(tx_id, TxStatus.COMMITTED, ok=True)

    def abort(self, tx_id: int) -> TxResult:
        """ABORT_REQUEST, FRM.rollback, lalu ABORT + flush, baru transaksi ditutup."""
        with self._lock:
            if tx_id not in self._active:
                return TxResult(tx_id, TxStatus.FAILED, ok=False, message="transaksi tidak aktif")
        self._frm.write_log(LogRecord(LogKind.ABORT_REQUEST, tx_id=tx_id))
        rb = self._frm.rollback(tx_id)
        if not rb.ok:
            return TxResult(tx_id, TxStatus.FAILED, ok=False, message=rb.message)
        res = self._frm.write_log(LogRecord(LogKind.ABORT, tx_id=tx_id))
        self._frm.flush_log(res.lsn)
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
