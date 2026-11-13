from _thread import LockType
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from threading import Lock
from common.transaction import AccessResponse, Action, ResourceId, TxStatus


@dataclass
class Transaction:
    tx_id: int
    timestamp: int
    status: TxStatus = TxStatus.ACTIVE
    abort_requested: bool = field(default=False, init=False)
    operation_lock: LockType = field(default_factory=Lock, init=False, repr=False, compare=False)


class ConcurrencyProtocol(ABC):
    @abstractmethod
    def on_begin(self, tx: Transaction) -> None: ...

    @abstractmethod
    def request(self, tx: Transaction, resource: ResourceId, action: Action) -> AccessResponse: ...

    @abstractmethod
    def on_finish(self, tx_id: int) -> None: ...
