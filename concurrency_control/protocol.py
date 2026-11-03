from abc import ABC, abstractmethod
from dataclasses import dataclass
from common.transaction import AccessResponse, Action, ResourceId, TxStatus


@dataclass
class Transaction:
    tx_id: int
    timestamp: int
    status: TxStatus = TxStatus.ACTIVE


class ConcurrencyProtocol(ABC):
    @abstractmethod
    def on_begin(self, tx: Transaction) -> None: ...

    @abstractmethod
    def request(self, tx: Transaction, resource: ResourceId, action: Action) -> AccessResponse: ...

    @abstractmethod
    def on_finish(self, tx_id: int) -> None: ...
