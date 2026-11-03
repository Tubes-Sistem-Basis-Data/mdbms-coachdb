from __future__ import annotations

from dataclasses import dataclass

from common.interfaces import ConcurrencyControlManager, StorageManager
from common.storage import RID
from common.transaction import AccessResponse, AccessStatus, Action, DataChange


@dataclass
class ExecContext:
    tx_id: int
    sm: StorageManager
    ccm: ConcurrencyControlManager
    access_intent: Action = Action.READ

    def authorize_table(self, table: str) -> AccessResponse:
        return AccessResponse(status=AccessStatus.ALLOWED)

    def authorize_rid(
        self,
        table: str,
        rid: RID,
        action: Action,
        change: DataChange | None = None,
    ) -> AccessResponse:
        return AccessResponse(status=AccessStatus.ALLOWED)

    def authorize_insert(self, change: DataChange) -> AccessResponse:
        return AccessResponse(status=AccessStatus.ALLOWED)
