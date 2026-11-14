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

    def validate_object(
        self, tx_id: int, resource: ResourceId, action: Action, change: DataChange | None = None,
    ) -> AccessResponse:
        if action is Action.WRITE and change is None:
            raise ValueError(f"T{tx_id}: WRITE ke {resource.table} wajib menyertakan DataChange")
        with self._mutex:
            tx = self._transactions.get(tx_id)
        if tx is None:
            return AccessResponse(AccessStatus.ABORTED, message=f"T{tx_id} tidak aktif")

        # Operasi satu transaksi tidak boleh mendahului log atau rollback transaksi itu.
        with tx.operation_lock:
            with self._mutex:
                if tx.status is not TxStatus.ACTIVE:
                    if tx.status is TxStatus.FAILED and tx.abort_requested:
                        raise MdbmsError(f"Rollback T{tx_id} belum selesai")
                    return AccessResponse(AccessStatus.ABORTED, message=f"T{tx_id} tidak aktif")
                response = self._protocol.request(tx, resource, action)
                if response.status is AccessStatus.ALLOWED:
                    self.schedule.record(tx_id, resource, action)
            if response.status is AccessStatus.ABORTED:
                return self._abort_access(tx, response.message)
            if response.status is AccessStatus.WAIT or change is None or action is Action.READ:
                return response
            try:
                lsn = self._bridge.log_write(
                    tx_id, change.op, change.table, change.rid, change.before, change.after,
                )
            except LogError as exc:
                return self._abort_access(tx, f"T{tx_id} dibatalkan karena pencatatan log gagal: {exc}")
            return AccessResponse(AccessStatus.ALLOWED, lsn=lsn)

    def commit(self, tx_id: int) -> TxResult:
        with self._mutex:
            tx = self._require_active(tx_id)
        if tx is None:
            return TxResult(tx_id, TxStatus.FAILED, ok=False, message=f"T{tx_id} tidak aktif")

        with tx.operation_lock:
            with self._mutex:
                if tx.status is not TxStatus.ACTIVE:
                    return TxResult(tx_id, TxStatus.FAILED, ok=False, message=f"T{tx_id} tidak aktif")
                victim = False
                if isinstance(self._protocol, LockManager):
                    victim = self._protocol.is_victim(tx_id)
                    if not victim:
                        self._protocol.cancel_wait(tx_id)
                if not victim:
                    tx.status = TxStatus.PARTIALLY_COMMITTED
            if victim:
                result = self._abort_transaction(tx)
                message = f"T{tx_id} korban deadlock"
                if not result.ok:
                    message += f". Rollback gagal: {result.message}"
                return TxResult(tx_id, result.status, ok=False, message=message)
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
                self.schedule.record_finish(tx_id, "COMMIT")
                self._finish(tx_id, TxStatus.COMMITTED)
            return TxResult(tx_id=tx_id, status=TxStatus.COMMITTED, ok=True)

    def abort(self, tx_id: int) -> TxResult:
        with self._mutex:
            tx = self._transactions.get(tx_id)
        if tx is None:
            return TxResult(tx_id, TxStatus.FAILED, ok=False, message=f"T{tx_id} tidak aktif")
        with tx.operation_lock:
            return self._abort_transaction(tx)

    def _abort_access(self, tx: Transaction, message: str) -> AccessResponse:
        result = self._abort_transaction(tx)
        if not result.ok:
            raise MdbmsError(f"{message}. Rollback gagal: {result.message}")
        return AccessResponse(AccessStatus.ABORTED, message=message)

    def _abort_transaction(self, tx: Transaction) -> TxResult:
        # Pemanggil sudah memegang operation_lock. Lock data ditahan selama undo.
        with self._mutex:
            if tx.status is TxStatus.ABORTED:
                return TxResult(tx.tx_id, TxStatus.ABORTED, ok=True)
            if tx.status is not TxStatus.ACTIVE and not tx.abort_requested:
                return TxResult(tx.tx_id, TxStatus.FAILED, ok=False, message=f"T{tx.tx_id} tidak bisa dibatalkan")
            tx.status = TxStatus.FAILED
            tx.abort_requested = True
            if isinstance(self._protocol, LockManager):
                self._protocol.cancel_wait(tx.tx_id)
        try:
            result = self._aborts.abort(tx.tx_id)
        except (MdbmsError, OSError) as exc:
            return TxResult(tx.tx_id, TxStatus.FAILED, ok=False, message=str(exc))
        if not result.ok or result.status is not TxStatus.ABORTED:
            return TxResult(tx.tx_id, TxStatus.FAILED, ok=False, message=result.message or "rollback gagal")
        with self._mutex:
            self.schedule.record_finish(tx.tx_id, "ABORT")
            self._finish(tx.tx_id, TxStatus.ABORTED)
        return result

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
