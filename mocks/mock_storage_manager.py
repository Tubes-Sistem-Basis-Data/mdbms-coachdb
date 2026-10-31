"""Mock Storage Manager: fungsi kosong."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Iterator, Optional

from common.interfaces.storage_manager import StorageManagerAPI
from common.types import (
    LSN,
    RID,
    IndexInfo,
    IndexKind,
    Row,
    TableSchema,
    TableStats,
    Value,
)

if TYPE_CHECKING:
    from common.interfaces.failure_recovery import (
        FailureRecoveryManager as FailureRecoveryAPI,
    )


class MockStorageManager(StorageManagerAPI):

    def __init__(self, data_dir: str | None = None) -> None:
        self.data_dir = data_dir
        self._rows: dict[str, list[Row]] = {}  # minimal 5 row per tabel, RID (0, i)
        self._schemas: dict[str, TableSchema] = {}  # student, course, enroll
        self._stats: dict[str, TableStats] = {}  # angka tetap

    def list_tables(self) -> list[str]:
        return []

    def get_table_schema(self, table: str) -> TableSchema:
        return None

    def get_stats(self, table: str) -> TableStats:
        return None

    def list_indexes(self, table: Optional[str] = None) -> list[IndexInfo]:
        return []

    def validate(self, table: str, values: tuple[Value, ...]) -> tuple[Value, ...]:
        return ()

    def scan(self, table: str) -> Iterator[Row]:
        return iter(())

    def fetch(self, table: str, rid: RID) -> Optional[Row]:
        return None

    def insert(
        self,
        table: str,
        values: tuple[Value, ...],
        log_fn: Callable[[RID], LSN],
    ) -> RID:
        return None

    def insert_at(self, table: str, rid: RID, values: tuple[Value, ...], lsn: LSN) -> None:
        return None

    def update(self, table: str, rid: RID, values: tuple[Value, ...], lsn: LSN) -> None:
        return None

    def delete(self, table: str, rid: RID, lsn: LSN) -> None:
        return None

    def truncate(self, table: str) -> int:
        return 0

    def create_index(self, name: str, table: str, column: str, kind: IndexKind) -> IndexInfo:
        return None

    def index_lookup(self, index_name: str, key: Value) -> list[RID]:
        return []

    def index_range(
        self,
        index_name: str,
        low: Optional[Value],
        high: Optional[Value],
    ) -> list[RID]:
        return []

    def rebuild_indexes(self) -> None:
        return None

    def attach_wal(self, frm: FailureRecoveryAPI) -> None:
        return None

    def flush_all(self) -> None:
        return None

    def snapshot_to(self, path: str) -> None:
        return None

    def restore_from(self, path: str) -> None:
        return None

    def close(self) -> None:
        return None
