from dataclasses import dataclass, field

from concurrency_control.protocol import ConcurrencyProtocol, Transaction
from common.transaction import AccessResponse, AccessStatus, Action, ResourceId


@dataclass
class TableTimestamps:
    read_ts: int = 0
    write_ts: int = 0
    uncommitted_writer: int | None = None
    waiters: list[int] = field(default_factory=list)


class TimestampProtocol(ConcurrencyProtocol):

    def __init__(self) -> None:
        self.tables: dict[ResourceId, TableTimestamps] = {}

    def on_begin(self, tx: Transaction) -> None:
        # Timestamp is assigned by CCManager. Nothing else to do.
        pass

    def on_finish(self, tx_id: int) -> None:
        for state in self.tables.values():
            if state.uncommitted_writer == tx_id:
                state.uncommitted_writer = None
            if tx_id in state.waiters:
                state.waiters.remove(tx_id)

    @staticmethod
    def _wait_dirty(tx: Transaction, resource: ResourceId, state: TableTimestamps) -> AccessResponse:
        return AccessResponse(
            AccessStatus.WAIT,
            message=f"T{tx.tx_id} menunggu T{state.uncommitted_writer} commit/abort pada {resource.table}",
        )

    def request(
        self,
        tx: Transaction,
        resource: ResourceId,
        action: Action,
    ) -> AccessResponse:
        state = self.tables.setdefault(resource, TableTimestamps())
        ts = tx.timestamp

        if action is Action.READ:
            if ts < state.write_ts:
                return AccessResponse(
                    AccessStatus.ABORTED,
                    message=(
                        f"T{tx.tx_id} (ts={ts}) lebih tua dari write_ts={state.write_ts} "
                        f"pada {resource.table}: READ ditolak (TSO)"
                    ),
                )
            if state.uncommitted_writer not in (None, tx.tx_id):
                return self._wait_dirty(tx, resource, state)
            state.read_ts = max(state.read_ts, ts)
            return AccessResponse(AccessStatus.ALLOWED)

        if action is Action.WRITE:
            if ts < state.read_ts:
                return AccessResponse(
                    AccessStatus.ABORTED,
                    message=(
                        f"T{tx.tx_id} (ts={ts}) lebih tua dari read_ts={state.read_ts} "
                        f"pada {resource.table}: WRITE ditolak (TSO)"
                    ),
                )
            if ts < state.write_ts:
                return AccessResponse(
                    AccessStatus.ABORTED,
                    message=(
                        f"T{tx.tx_id} (ts={ts}) lebih tua dari write_ts={state.write_ts} "
                        f"pada {resource.table}: WRITE usang (TSO)"
                    ),
                )

            if state.uncommitted_writer not in (None, tx.tx_id):
                return self._wait_dirty(tx, resource, state)
            state.write_ts = max(state.write_ts, ts)
            state.uncommitted_writer = tx.tx_id
            return AccessResponse(AccessStatus.ALLOWED)

        return AccessResponse(
            AccessStatus.ABORTED,
            message=f"T{tx.tx_id}: aksi {action!r} tidak didukung protokol timestamp",
        )
