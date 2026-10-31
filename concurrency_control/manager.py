import threading
from common.interfaces import ConcurrencyControlManager, FailureRecoveryManager
from common.transaction import (
    AccessResponse,
    Action,
    DataChange,
    ProtocolType,
    ResourceId,
    TxResult,
    TxStatus,
)
from concurrency_control.frm_bridge import AbortCoordinator, FrmBridge
from concurrency_control.protocol import ConcurrencyProtocol, Transaction


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
        # TODO(CC1): replace with self._make_protocol(protocol) once _make_protocol exists
        self._protocol: ConcurrencyProtocol | None = None
        # TODO(CC4): self.schedule = ScheduleRecorder() once serializability.py exists

    def begin_transaction(self) -> int:
        #Assign tx_id = last_tx_id + 1, log BEGIN.
        #Blocks while `pause_new_transactions` active.
        raise NotImplementedError

    def validate_object(self, tx_id: int, resource: ResourceId, action: Action,change: DataChange | None = None,) -> AccessResponse:
        #Request access permission from the active protocol.
        #- tx not active, or WRITE without `change` -> ABORTED.
        #- protocol ALLOWED + WRITE -> write exactly one DATA log via the bridge, fill `lsn`.
        #- protocol ALLOWED -> `self.schedule.record(tx_id, resource, action)` (CC-09).
        #- protocol ABORTED -> run `self.abort(tx_id)` first, then return ABORTED
        raise NotImplementedError

    def commit(self, tx_id: int) -> TxResult:
        #Log COMMIT + flush via the bridge, then `protocol.on_finish`, close the transaction
        raise NotImplementedError

    def abort(self, tx_id: int) -> TxResult:
        #`bridge.abort` (ABORT_REQUEST, rollback, ABORT + flush), then release locks
        #Locks are released only after rollback succeeds
        raise NotImplementedError

    def _require_active(self, tx_id: int) -> Transaction | None:
        #Return the ACTIVE transaction with `tx_id`, or None. Call with `_mutex` held
        raise NotImplementedError

    def _finish(self, tx_id: int, status: TxStatus) -> None:
        #Set the final status, remove from the active table, `protocol.on_finish`, notify `_cond`
        raise NotImplementedError

    def set_protocol(self, protocol: ProtocolType) -> None:
        #Switch protocol. Raise `MdbmsError` if any transaction is still active
        raise NotImplementedError

    def get_protocol(self) -> ProtocolType:
        #The currently active protocol
        raise NotImplementedError

    @staticmethod
    def _make_protocol(protocol: ProtocolType) -> ConcurrencyProtocol:
        #Create the protocol instance: LOCK_BASED -> `LockManager`, TIMESTAMP_BASED -> `TimestampProtocol`
        raise NotImplementedError

    def initialize(self, last_tx_id: int) -> None:
        #Set the tx_id counter from recovery; next tx = last_tx_id + 1. (PIC CC3)
        raise NotImplementedError

    def pause_new_transactions(self) -> None:
        #Hold subsequent `begin_transaction` calls
        raise NotImplementedError

    def wait_until_idle(self) -> None:
        #Block until no transaction is active
        raise NotImplementedError

    def resume_transactions(self) -> None:
        #Allow new transactions again and wake up the blocked ones
        raise NotImplementedError
