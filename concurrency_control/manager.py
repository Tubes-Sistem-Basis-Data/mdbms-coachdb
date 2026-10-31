import threading
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
        # self._make_protocol(protocol) once _make_protocol
        self._protocol: ConcurrencyProtocol | None = None
        self.schedule = ScheduleRecorder()

    def begin_transaction(self) -> int:
        #Assign tx_id = last_tx_id + 1, log BEGIN.
        #Blocks while `pause_new_transactions` active.
        return 0

    def validate_object(self, tx_id: int, resource: ResourceId, action: Action,change: DataChange | None = None,) -> AccessResponse:
        # Request access permission from the active protocol.
        #- tx not active, or WRITE without `change` -> ABORTED.
        #- protocol ALLOWED + WRITE -> write one DATA log via bridge, fill `lsn`.
        #- protocol ALLOWED -> `self.schedule.record(tx_id, resource, action)` (CC-09).
        #- protocol ABORTED -> run `self.abort(tx_id)`, return ABORTED
        return AccessResponse(status=AccessStatus.ALLOWED)

    def commit(self, tx_id: int) -> TxResult:
        #Log COMMIT + flush via the bridge, then `protocol.on_finish`, close the transaction
        return TxResult(tx_id=tx_id, status=TxStatus.COMMITTED, ok=True)

    def abort(self, tx_id: int) -> TxResult:
        #`bridge.abort` (ABORT_REQUEST, rollback, ABORT + flush), then release locks
        #Locks are released only after rollback succeeds
        return TxResult(tx_id=tx_id, status=TxStatus.ABORTED, ok=False)

    def _require_active(self, tx_id: int) -> Transaction | None:
        #Return the ACTIVE transaction with `tx_id`, or None. Call with `_mutex` held
        return None

    def _finish(self, tx_id: int, status: TxStatus) -> None:
        #Set the final status, remove from the active table, `protocol.on_finish`, notify `_cond`
        return None

    def set_protocol(self, protocol: ProtocolType) -> None:
        #Switch protocol. Raise `MdbmsError` if any transaction is still active
        return None

    def get_protocol(self) -> ProtocolType:
        #The currently active protocol
        return self._protocol_type

    @staticmethod
    def _make_protocol(protocol: ProtocolType) -> ConcurrencyProtocol:
        #Create the protocol instance: LOCK_BASED -> `LockManager`, TIMESTAMP_BASED -> `TimestampProtocol`
        return LockManager()

    def initialize(self, last_tx_id: int) -> None:
        #Set the tx_id counter from recovery; next tx = last_tx_id + 1. (PIC CC3)
        return None

    def pause_new_transactions(self) -> None:
        #Hold subsequent `begin_transaction` calls
        return None

    def wait_until_idle(self) -> None:
        #Block until no transaction is active
        return None

    def resume_transactions(self) -> None:
        #Allow new transactions again and wake up the blocked ones
        return None
