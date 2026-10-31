"""Facade Storage Manager."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, Iterator

from common.catalog import IndexInfo, TableSchema, TableStats
from common.storage import RID, Row


class StorageManager(ABC):
    """Akses data, katalog, dan index. Dipakai QO (katalog), QP (data), FRM (undo/redo)."""

    @abstractmethod
    def list_tables(self) -> list[str]:
        """Daftar nama tabel."""

    @abstractmethod
    def get_schema(self, table: str) -> TableSchema:
        """Skema tabel. Raise SemanticError jika tabel tidak ada."""

    @abstractmethod
    def get_stats(self, table: str) -> TableStats:
        """Statistik tabel untuk cost model QO."""

    @abstractmethod
    def scan(self, table: str) -> Iterator[Row]:
        """Iterasi seluruh baris tabel."""

    @abstractmethod
    def fetch(self, table: str, rid: RID) -> Row | None:
        """Ambil satu baris lewat RID, None jika tidak ada."""

    @abstractmethod
    def allocate_rid(self, table: str) -> RID:
        """Pesan RID untuk INSERT (langkah pertama INSERT dua langkah)."""

    @abstractmethod
    def release_rid(self, rid: RID) -> None:
        """Lepas RID yang dipesan tapi batal dipakai."""

    @abstractmethod
    def insert(self, table: str, rid: RID, values: list, lsn: int | None) -> None:
        """Tulis baris di RID. `lsn=None` saat dipanggil FRM untuk undo/redo."""

    @abstractmethod
    def update(self, table: str, rid: RID, values: list, lsn: int | None) -> None:
        """Ganti seluruh nilai baris di RID."""

    @abstractmethod
    def delete(self, table: str, rid: RID, lsn: int | None) -> None:
        """Hapus baris di RID."""

    @abstractmethod
    def delete_all(self, table: str, lsn: int | None) -> None:
        """Kosongkan tabel."""

    @abstractmethod
    def create_index(self, info: IndexInfo) -> None:
        """Bangun index dan catat di katalog."""

    @abstractmethod
    def index_lookup(self, table: str, column: str, key) -> list[RID]:
        """RID dengan nilai kolom sama dengan `key`."""

    @abstractmethod
    def index_range(self, table: str, column: str, low, high) -> list[RID]:
        """RID dengan low <= nilai <= high. low/high None berarti tak terbatas."""

    @abstractmethod
    def flush(self, page_ids: list[int] | None = None) -> None:
        """Tulis page dirty ke disk (semua jika None). Memanggil log flusher dulu."""

    @abstractmethod
    def set_log_flusher(self, fn: Callable[[int], bool]) -> None:
        """Pasang callback `flush_log(page_lsn)` dari FRM. Dipanggil main.py."""
