from __future__ import annotations

from common.interfaces import FailureRecoveryManager
from common.storage import RID
from common.transaction import ChangeOp, TxResult


class FrmBridge:
    def __init__(self, frm: FailureRecoveryManager) -> None:
        self._frm = frm

    def log_begin(self, tx_id: int) -> int:
        raise NotImplementedError

    def log_write(
        self, tx_id: int, op: ChangeOp, table: str, rid: RID, before, after
    ) -> int:
        raise NotImplementedError

    def log_commit(self, tx_id: int) -> int:
        raise NotImplementedError

    def rollback(self, tx_id: int) -> TxResult:
        raise NotImplementedError


class AbortCoordinator:
    def __init__(self, bridge: FrmBridge) -> None:
        self._bridge = bridge

    def abort(self, tx_id: int) -> TxResult:
        raise NotImplementedError

    def mark_victim(self, tx_id: int) -> None:
        raise NotImplementedError
