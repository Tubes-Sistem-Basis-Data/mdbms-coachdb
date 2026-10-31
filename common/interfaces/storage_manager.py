from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Callable, Iterator, Optional

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


class StorageManagerAPI(ABC):
    """Akses data, katalog, dan index. Dipakai QO (katalog), QP (data), FRM (undo/redo)."""

    @abstractmethod
    def list_tables(self) -> list[str]:
        """Daftar nama tabel. Dipanggil QO."""

    @abstractmethod
    def get_table_schema(self, table: str) -> TableSchema:
        """Skema tabel. Raise TableNotFoundError jika tabel tidak ada. Dipanggil QO, QP."""

    @abstractmethod
    def get_stats(self, table: str) -> TableStats:
        """Statistik tabel untuk cost model. Dipanggil QO."""

    @abstractmethod
    def list_indexes(self, table: Optional[str] = None) -> list[IndexInfo]:
        """Daftar index, semua tabel jika `table` None. Dipanggil QO, QP."""

    @abstractmethod
    def validate(self, table: str, values: tuple[Value, ...]) -> tuple[Value, ...]:
        """Cek nilai terhadap skema dan PK, return nilai ternormalisasi. Dipanggil QP sebelum log_write."""

    @abstractmethod
    def scan(self, table: str) -> Iterator[Row]:
        """Iterasi seluruh baris LIVE, urut (page_id, slot). Dipanggil QP."""

    @abstractmethod
    def fetch(self, table: str, rid: RID) -> Optional[Row]:
        """Ambil satu baris lewat RID, None jika slot kosong atau tombstone. Dipanggil QP, FRM."""

    @abstractmethod
    def insert(
        self,
        table: str,
        values: tuple[Value, ...],
        log_fn: Callable[[RID], LSN],
    ) -> RID:
        """Validasi, pilih slot, panggil `log_fn(rid)` untuk LSN, lalu tulis. Dipanggil QP."""

    @abstractmethod
    def insert_at(self, table: str, rid: RID, values: tuple[Value, ...], lsn: LSN) -> None:
        """Tulis baris di RID tertentu, idempoten. Dipanggil FRM (undo DELETE, redo INSERT)."""

    @abstractmethod
    def update(self, table: str, rid: RID, values: tuple[Value, ...], lsn: LSN) -> None:
        """Ganti seluruh nilai baris di RID, idempoten. Dipanggil QP, FRM."""

    @abstractmethod
    def delete(self, table: str, rid: RID, lsn: LSN) -> None:
        """Jadikan slot tombstone, idempoten. Dipanggil QP, FRM."""

    @abstractmethod
    def truncate(self, table: str) -> int:
        """Kosongkan tabel, return jumlah record yang dihapus. Dipanggil QP."""

    @abstractmethod
    def create_index(self, name: str, table: str, column: str, kind: IndexKind) -> IndexInfo:
        """Bangun index dan catat di katalog. Dipanggil QP."""

    @abstractmethod
    def index_lookup(self, index_name: str, key: Value) -> list[RID]:
        """RID dengan nilai kolom sama dengan `key`. Dipanggil QP."""

    @abstractmethod
    def index_range(
        self,
        index_name: str,
        low: Optional[Value],
        high: Optional[Value],
    ) -> list[RID]:
        """RID dengan low <= nilai <= high. None berarti tak terbatas. Dipanggil QP."""

    @abstractmethod
    def rebuild_indexes(self) -> None:
        """Bangun ulang semua index dari isi tabel. Dipanggil main.py setelah recovery."""

    @abstractmethod
    def attach_wal(self, frm: FailureRecoveryAPI) -> None:
        """Pasang FRM untuk `flush_log_until` sebelum page ditulis. Dipanggil main.py."""

    @abstractmethod
    def flush_all(self) -> None:
        """Tulis semua page dirty ke disk. Dipanggil FRM."""

    @abstractmethod
    def snapshot_to(self, path: str) -> None:
        """Salin seluruh data ke `path`. Dipanggil FRM (checkpoint, PITR) dan seeder."""

    @abstractmethod
    def restore_from(self, path: str) -> None:
        """Ganti seluruh data dengan snapshot di `path`. Dipanggil FRM (PITR)."""

    @abstractmethod
    def close(self) -> None:
        """Flush dan tutup semua file. Dipanggil main.py saat shutdown."""

StorageManager = StorageManagerAPI