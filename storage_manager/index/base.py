from __future__ import annotations

from abc import ABC, abstractmethod

from common.storage import RID
from storage_manager.types import Value

Key = int | float | str


class Index(ABC):
    def __init__(self, table: str, column: str) -> None:
        self.table: str = table
        self.column: str = column

    @abstractmethod
    def insert(self, key: Value, rid: RID) -> None: ...

    @abstractmethod
    def delete(self, key: Value, rid: RID) -> None: ...

    @abstractmethod
    def search(self, key: Value) -> list[RID]: ...

    @abstractmethod
    def range(self, low: Value | None, high: Value | None) -> list[RID]: ...

    @abstractmethod
    def height(self) -> int: ...
