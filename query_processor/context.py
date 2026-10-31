from __future__ import annotations

from dataclasses import dataclass

from common.interfaces import ConcurrencyControlManager, StorageManager
from common.storage import RID
from common.transaction import AccessResponse, Action, DataChange


@dataclass
class ExecContext:
    tx_id: int
    sm: StorageManager
    ccm: ConcurrencyControlManager
    access_intent: Action = Action.READ

    def authorize_table(self, table: str) -> AccessResponse:
        raise NotImplementedError("QP2: authorize_table belum diimplementasikan")

    def authorize_rid(
        self,
        table: str,
        rid: RID,
        action: Action,
        change: DataChange | None = None,
    ) -> AccessResponse:
        raise NotImplementedError("QP2: authorize_rid belum diimplementasikan")

    def authorize_insert(self, change: DataChange) -> AccessResponse:
        raise NotImplementedError("QP2: authorize_insert belum diimplementasikan")
