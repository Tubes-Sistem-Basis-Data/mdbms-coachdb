from dataclasses import dataclass, field
from concurrency_control.protocol import ConcurrencyProtocol, Transaction
from common.transaction import AccessResponse, Action, ResourceId


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
        pass

    def request(self, tx: Transaction, resource: ResourceId, action: Action,) -> AccessResponse:
        ...

    def on_write_logged(self, tx: Transaction, op: str, table: str, rid: ResourceId,) -> None:
        ...

    def release(self, tx: Transaction) -> list[Transaction]:
        ...

    def cancel_wait(self, tx: Transaction) -> None:
        ...

    def on_finish(self, tx_id: int) -> None:
        ...