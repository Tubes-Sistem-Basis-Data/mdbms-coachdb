import threading
from common.errors import LogError, MdbmsError
from common.interfaces import ConcurrencyControlManager, FailureRecoveryManager
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
from concurrency_control.frm_bridge import AbortCoordinator, FrmBridge
from concurrency_control.lock_manager import LockManager
from concurrency_control.protocol import ConcurrencyProtocol, Transaction
from concurrency_control.serializability import ScheduleRecorder
from concurrency_control.timestamp import TimestampProtocol


class ProtocolSwitchError(MdbmsError):
    """Ganti protokol saat masih ada transaksi aktif."""


class CCManager(ConcurrencyControlManager):
    def __init__(self, frm: FailureRecoveryManager, protocol: ProtocolType = ProtocolType.LOCK_BASED,) -> None:
        self._bridge = FrmBridge(frm)
        self._aborts = AbortCoordinator(self._bridge)
        self._frm = frm
        self._mutex = threading.Lock()
        self._cond = threading.Condition(self._mutex)
        self._last_tx_id = 0
        self._next_ts = 0
        self._transactions: dict[int, Transaction] = {}
        self._paused = False
        self._protocol_type = protocol
        self._protocol: ConcurrencyProtocol = self._make_protocol(protocol)
        self.schedule = ScheduleRecorder()

    def begin_transaction(self) -> int:
        with self._cond:
            while self._paused:
                self._cond.wait()
            self._last_tx_id += 1
            self._next_ts += 1
            tx = Transaction(tx_id=self._last_tx_id, timestamp=self._next_ts)
            self._transactions[tx.tx_id] = tx
            self._protocol.on_begin(tx)
        try:
            self._bridge.log_begin(tx.tx_id)
        except Exception:
            with self._mutex:
                self._finish(tx.tx_id, TxStatus.FAILED)
            raise
        return tx.tx_id

    def validate_object(self, tx_id: int, resource: ResourceId, action: Action,change: DataChange | None = None,) -> AccessResponse:
        if action is Action.WRITE and change is None:
            raise ValueError(f"T{tx_id}: WRITE ke {resource.table} wajib menyertakan DataChange")
        with self._mutex:
            tx = self._require_active(tx_id)
            if tx is None:
                return AccessResponse(status=AccessStatus.ABORTED, message=f"T{tx_id} tidak aktif")
            response = self._protocol.request(tx, resource, action)
            if response.status is not AccessStatus.ALLOWED:
                return response
            self.schedule.record(tx_id, resource, action)
        if action is Action.READ:
            return response
        try:
            lsn = self._bridge.log_write(tx_id, change.op, change.table, change.rid, change.before, change.after)
        except LogError as exc:
            self.abort(tx_id)
            return AccessResponse(
                status=AccessStatus.ABORTED,
                message=f"T{tx_id} dibatalkan karena pencatatan log gagal: {exc}",
            )
        return AccessResponse(status=AccessStatus.ALLOWED, lsn=lsn)

    def commit(self, tx_id: int) -> TxResult:
        with self._mutex:
            tx = self._require_active(tx_id)
            if tx is None:
                return TxResult(tx_id=tx_id, status=TxStatus.FAILED, ok=False, message=f"T{tx_id} tidak aktif")
            tx.status = TxStatus.PARTIALLY_COMMITTED
        try:
            self._bridge.log_commit(tx_id)
        except Exception as exc:
            with self._mutex:
                tx.status = TxStatus.FAILED
            return TxResult(
                tx_id=tx_id,
                status=TxStatus.FAILED,
                ok=False,
                message=f"Status commit T{tx_id} tidak pasti, cek setelah restart: {exc}",
            )
        with self._mutex:
            self._finish(tx_id, TxStatus.COMMITTED)
        return TxResult(tx_id=tx_id, status=TxStatus.COMMITTED, ok=True)

    def abort(self, tx_id: int) -> TxResult:
        # bridge.abort (ABORT_REQUEST, rollback, ABORT + flush), baru lepas lock
        # lock cuma dilepas kalau rollback berhasil
        return TxResult(tx_id=tx_id, status=TxStatus.ABORTED, ok=False)

    def _require_active(self, tx_id: int) -> Transaction | None:
        tx = self._transactions.get(tx_id)
        return tx if tx is not None and tx.status is TxStatus.ACTIVE else None

    def _finish(self, tx_id: int, status: TxStatus) -> None:
        tx = self._transactions.pop(tx_id, None)
        if tx is not None:
            tx.status = status
        self._protocol.on_finish(tx_id)
        self._cond.notify_all()

    def set_protocol(self, protocol: ProtocolType) -> None:
        with self._mutex:
            if self._transactions:
                raise ProtocolSwitchError(
                    f"Protokol tidak bisa diganti saat masih ada {len(self._transactions)} transaksi aktif"
                )
            if protocol is self._protocol_type:
                return
            self._protocol = self._make_protocol(protocol)
            self._protocol_type = protocol

    def get_protocol(self) -> ProtocolType:
        return self._protocol_type

    @staticmethod
    def _make_protocol(protocol: ProtocolType) -> ConcurrencyProtocol:
        if protocol is ProtocolType.TIMESTAMP_BASED:
            return TimestampProtocol()
        return LockManager()

    def initialize(self, last_tx_id: int) -> None:
        # set counter tx_id dari hasil recovery, tx berikutnya last_tx_id + 1 (PIC CC3)
        return None

    def pause_new_transactions(self) -> None:
        with self._mutex:
            self._paused = True

    def wait_until_idle(self) -> None:
        with self._cond:
            while self._transactions:
                self._cond.wait()

    def resume_transactions(self) -> None:
        with self._cond:
            self._paused = False
            self._cond.notify_all()
