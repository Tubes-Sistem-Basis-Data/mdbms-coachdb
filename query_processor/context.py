from __future__ import annotations

from dataclasses import dataclass, field
from time import sleep

from common import config
from common.errors import TransactionAbortedError
from common.expression import Subquery
from common.interfaces import ConcurrencyControlManager, StorageManager
from common.storage import RID
from common.transaction import (
    NO_RECORD,
    AccessResponse,
    AccessStatus,
    Action,
    ChangeOp,
    DataChange,
    ResourceId,
)
from query_processor.errors import ExecutionError
from query_processor.subqueries import ScalarResolver, ScalarValue


@dataclass
class ExecContext:
    tx_id: int | None
    sm: StorageManager
    ccm: ConcurrencyControlManager
    access_intent: Action = Action.READ
    scalar_resolver: ScalarResolver | None = field(default=None, repr=False, compare=False)
    _scalar_cache: dict[int, tuple[Subquery, ScalarValue]] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )

    def resolve_scalar(self, subquery: Subquery) -> ScalarValue:
        if not isinstance(subquery, Subquery):
            raise ExecutionError("Resolver skalar memerlukan Subquery")
        if type(self.tx_id) is not int or self.tx_id <= 0:
            raise ExecutionError("Subquery skalar memerlukan transaksi aktif")
        cached = self._scalar_cache.get(id(subquery))
        if cached is not None:
            return cached[1]
        if self.scalar_resolver is None:
            raise ExecutionError("Resolver subquery skalar belum tersedia")
        result = self.scalar_resolver(subquery)
        # Simpan node agar id-nya tidak dipakai ulang selama statement.
        self._scalar_cache[id(subquery)] = (subquery, result)
        return result

    def authorize_table(self, table: str) -> AccessResponse:
        # Scan dikunci READ; perubahan dicatat terpisah per RID.
        return self._authorize(ResourceId(table), Action.READ)

    def authorize_rid(
        self,
        table: str,
        rid: RID,
        action: Action,
        change: DataChange | None = None,
    ) -> AccessResponse:
        if not isinstance(rid, RID) or rid.table != table:
            raise ExecutionError("RID tidak sesuai dengan tabel akses")
        resource = ResourceId(table, rid)
        if action is Action.WRITE:
            self._validate_change(resource, change)
        elif action is not Action.READ or change is not None:
            raise ExecutionError("READ tidak boleh membawa perubahan data")
        return self._authorize(resource, action, change)

    def authorize_insert(self, change: DataChange) -> AccessResponse:
        if not isinstance(change, DataChange) or change.op is not ChangeOp.INSERT:
            raise ExecutionError("authorize_insert memerlukan perubahan INSERT")
        return self.authorize_rid(change.table, change.rid, Action.WRITE, change)

    @staticmethod
    def _validate_change(resource: ResourceId, change: DataChange | None) -> None:
        if not isinstance(change, DataChange):
            raise ExecutionError("WRITE memerlukan DataChange")
        if change.table != resource.table or change.rid != resource.rid:
            raise ExecutionError("DataChange tidak sesuai dengan objek akses")
        if change.op is ChangeOp.INSERT:
            valid = change.before is NO_RECORD and isinstance(change.after, list)
        elif change.op is ChangeOp.UPDATE:
            valid = isinstance(change.before, list) and isinstance(change.after, list)
        elif change.op is ChangeOp.DELETE:
            valid = isinstance(change.before, list) and change.after is NO_RECORD
        else:
            valid = False
        if not valid:
            raise ExecutionError("Operasi atau before/after image DataChange tidak valid")

    def _authorize(
        self,
        resource: ResourceId,
        action: Action,
        change: DataChange | None = None,
    ) -> AccessResponse:
        if type(self.tx_id) is not int or self.tx_id <= 0:
            raise ExecutionError("Izin akses memerlukan transaksi aktif")
        if not isinstance(resource.table, str) or not resource.table:
            raise ExecutionError("Nama tabel akses tidak valid")
        while True:
            response = self.ccm.validate_object(self.tx_id, resource, action, change)
            if not isinstance(response, AccessResponse):
                raise ExecutionError("Respons izin akses CCM tidak valid")
            if response.status is AccessStatus.WAIT:
                sleep(config.WAIT_RETRY_SECONDS)
                continue
            if response.status is AccessStatus.ABORTED:
                raise TransactionAbortedError(
                    response.message or "Transaksi dibatalkan oleh CCM", self.tx_id
                )
            if response.status is not AccessStatus.ALLOWED:
                raise ExecutionError("Status izin akses CCM tidak valid")
            if action is Action.WRITE and (
                type(response.lsn) is not int or response.lsn <= 0
            ):
                raise ExecutionError("Izin WRITE dari CCM tidak menyertakan LSN yang valid")
            return response
