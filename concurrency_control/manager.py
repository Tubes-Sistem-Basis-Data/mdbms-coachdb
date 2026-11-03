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
        # nanti diisi self._make_protocol(protocol)
        self._protocol: ConcurrencyProtocol | None = None
        self.schedule = ScheduleRecorder()

    def begin_transaction(self) -> int:
        # tx_id = last_tx_id + 1, tulis log BEGIN
        # ditahan selama pause_new_transactions aktif
        return 0

    def validate_object(self, tx_id: int, resource: ResourceId, action: Action,change: DataChange | None = None,) -> AccessResponse:
        # minta izin ke protokol aktif
        # - tx tidak aktif, atau WRITE tanpa change -> ABORTED
        # - ALLOWED + WRITE -> tulis satu log DATA lewat bridge, isi lsn
        # - ALLOWED -> self.schedule.record(tx_id, resource, action) (CC-09)
        # - ABORTED -> jalankan self.abort(tx_id), balikin ABORTED
        return AccessResponse(status=AccessStatus.ALLOWED)

    def commit(self, tx_id: int) -> TxResult:
        # log COMMIT + flush lewat bridge, lalu protocol.on_finish, tutup transaksi
        return TxResult(tx_id=tx_id, status=TxStatus.COMMITTED, ok=True)

    def abort(self, tx_id: int) -> TxResult:
        # bridge.abort (ABORT_REQUEST, rollback, ABORT + flush), baru lepas lock
        # lock cuma dilepas kalau rollback berhasil
        return TxResult(tx_id=tx_id, status=TxStatus.ABORTED, ok=False)

    def _require_active(self, tx_id: int) -> Transaction | None:
        # transaksi ACTIVE dengan tx_id ini, None kalau tidak ada. Panggil saat pegang _mutex
        return None

    def _finish(self, tx_id: int, status: TxStatus) -> None:
        # set status akhir, hapus dari tabel aktif, protocol.on_finish, notify _cond
        return None

    def set_protocol(self, protocol: ProtocolType) -> None:
        # ganti protokol, raise MdbmsError kalau masih ada transaksi aktif
        return None

    def get_protocol(self) -> ProtocolType:
        # protokol yang sedang aktif
        return self._protocol_type

    @staticmethod
    def _make_protocol(protocol: ProtocolType) -> ConcurrencyProtocol:
        # LOCK_BASED -> LockManager, TIMESTAMP_BASED -> TimestampProtocol
        return LockManager()

    def initialize(self, last_tx_id: int) -> None:
        # set counter tx_id dari hasil recovery, tx berikutnya last_tx_id + 1 (PIC CC3)
        return None

    def pause_new_transactions(self) -> None:
        # tahan begin_transaction berikutnya
        return None

    def wait_until_idle(self) -> None:
        # tunggu sampai tidak ada transaksi aktif
        return None

    def resume_transactions(self) -> None:
        # izinkan transaksi baru lagi dan bangunkan yang tertahan
        return None
