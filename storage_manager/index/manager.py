from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, Iterator, Optional, Union

from common.catalog import IndexInfo, IndexKind
from common.storage import RID, Row

Value = Union[int, float, str, None]


class Index(ABC):

    def __init__(self, table: str, column: str) -> None:
        self.table: str = table
        self.column: str = column

    @abstractmethod
    def insert(self, key: Value, rid: RID) -> None:
        ...

    @abstractmethod
    def delete(self, key: Value, rid: RID) -> None:
        ...

    @abstractmethod
    def search(self, key: Value) -> list[RID]:
        ...

    @abstractmethod
    def range(self, low: Optional[Value], high: Optional[Value]) -> list[RID]:
        ...

    @abstractmethod
    def height(self) -> int:
        ...


class IndexManager:
    def __init__(
        self,
        catalog: "Catalog", 
        scan_fn: Callable[[str], Iterator[Row]],
    ) -> None:
        self._catalog = catalog
        self._scan_fn = scan_fn
        self._indexes: dict[str, Index] = {}

    def create_index(
        self, name: str, table: str, column: str, kind: IndexKind
    ) -> IndexInfo:
        raise NotImplementedError

    def lookup(self, name: str, key: Value) -> list[RID]:
        raise NotImplementedError

    def range(
        self, name: str, low: Optional[Value], high: Optional[Value]
    ) -> list[RID]:
        raise NotImplementedError

    def height(self, name: str) -> int:
        raise NotImplementedError

    def on_insert(self, table: str, values: tuple[Value, ...], rid: RID) -> None:
        raise NotImplementedError

    def on_delete(self, table: str, values: tuple[Value, ...], rid: RID) -> None:
        raise NotImplementedError

    def on_update(
        self,
        table: str,
        old: tuple[Value, ...],
        new: tuple[Value, ...],
        rid: RID,
    ) -> None:
        raise NotImplementedError

    def rebuild_all(self) -> None:
        raise NotImplementedError
