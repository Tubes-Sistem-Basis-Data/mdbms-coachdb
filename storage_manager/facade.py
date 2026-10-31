"""Facade Storage Manager milik Group 1."""

from __future__ import annotations

import os
import threading
from typing import Callable, Iterator

from common import config
from common.catalog import IndexInfo, TableSchema, TableStats
from common.interfaces import StorageManager as StorageManagerBase
from common.storage import RID, Row
from storage_manager.buffer import BufferPool
from storage_manager.catalog import Catalog
from storage_manager.crud import RecordManager
from storage_manager.disk import DiskManager
from storage_manager.index import IndexManager
from storage_manager.stats import StatisticsManager
from storage_manager.types import Value

CATALOG_FILE = "catalog.json"


class StorageManager(StorageManagerBase):
    """Wiring disk, buffer, katalog, record, index, dan statistik."""

    def __init__(
        self,
        data_dir: str = config.DATA_DIR,
        buffer_frames: int = config.BUFFER_FRAMES,
    ) -> None:
        self._lock = threading.RLock()
        self._disk = DiskManager(data_dir)
        self._buffer = BufferPool(self._disk, buffer_frames)
        self._catalog = Catalog(os.path.join(data_dir, CATALOG_FILE))
        self._stats = StatisticsManager(self._catalog, lambda t: self._records.scan(t))
        self._index_manager = IndexManager(self._catalog, lambda t: self._records.scan(t))
        self._records = RecordManager(
            self._lock, self._buffer, self._catalog, self._index_manager, self._stats
        )
        self._log_flusher: Callable[[int], bool] | None = None

    # Kontrak StorageManager

    def list_tables(self) -> list[str]:
        """Daftar nama tabel di katalog. Dipanggil QO."""
        return []

    def get_schema(self, table: str) -> TableSchema:
        """Skema tabel. Raise SemanticError jika tabel tidak ada. Dipanggil QO."""
        return TableSchema(name=table, columns=[])

    def get_stats(self, table: str) -> TableStats:
        """Statistik tabel untuk cost model. Dipanggil QO."""
        return TableStats(table=table, n_r=0, b_r=0, l_r=0.0, f_r=0, distinct={})

    def scan(self, table: str) -> Iterator[Row]:
        """Iterasi seluruh baris tabel. Dipanggil QP."""
        return iter(())

    def fetch(self, table: str, rid: RID) -> Row | None:
        """Ambil satu baris lewat RID, None jika tidak ada. Dipanggil QP dan FRM."""
        return None

    def allocate_rid(self, table: str) -> RID:
        """Pesan slot kosong untuk INSERT. Dipanggil QP sebelum minta izin CCM."""
        return RID(table=table, page_id=0, slot=0)

    def release_rid(self, rid: RID) -> None:
        """Lepas RID yang batal dipakai karena INSERT tidak diizinkan. Dipanggil QP."""
        return None

    def insert(self, table: str, rid: RID, values: list, lsn: int | None) -> None:
        """Tulis baris di RID, idempoten. `lsn=None` saat undo/redo dari FRM."""
        return None

    def update(self, table: str, rid: RID, values: list, lsn: int | None) -> None:
        """Ganti seluruh nilai baris di RID, idempoten. Dipanggil QP dan FRM."""
        return None

    def delete(self, table: str, rid: RID, lsn: int | None) -> None:
        """Hapus baris di RID (tombstone), idempoten. Dipanggil QP dan FRM."""
        return None

    def delete_all(self, table: str, lsn: int | None) -> None:
        """Hapus seluruh baris tabel. Dipanggil QP untuk DELETE tanpa WHERE."""
        return None

    def create_index(self, info: IndexInfo) -> None:
        """Bangun index dan catat di katalog. Dipanggil QP."""
        return None

    def index_lookup(self, table: str, column: str, key: Value) -> list[RID]:
        """RID dengan nilai kolom sama dengan `key`. Dipanggil QP."""
        return []

    def index_range(self, table: str, column: str, low: Value, high: Value) -> list[RID]:
        """RID dengan low <= nilai <= high, None berarti tak terbatas. Dipanggil QP."""
        return []

    def flush(self, page_ids: list[int] | None = None) -> None:
        """Tulis page dirty ke disk, semua page jika `page_ids` None. Dipanggil FRM."""
        return None

    def set_log_flusher(self, fn: Callable[[int], bool]) -> None:
        """Pasang callback flush_log dari FRM ke buffer pool. Dipanggil main.py."""
        self._log_flusher = fn
        self._buffer.set_log_flusher(fn)

    # Tambahan SM, di luar kontrak

    def list_indexes(self, table: str | None = None) -> list[IndexInfo]:
        """Daftar index, bisa difilter per tabel."""
        return []

    def validate(self, table: str, values: list) -> list:
        """Cek nilai sesuai skema, raise SchemaViolationError jika tidak."""
        return []

    def bulk_load(self, table: str, rows: list[list]) -> int:
        """Isi banyak baris sekaligus tanpa log. Dipakai seeder."""
        return 0

    def rebuild_indexes(self) -> None:
        """Bangun ulang semua index dari isi tabel setelah recovery."""
        return None

    def snapshot_to(self, path: str) -> None:
        """Salin seluruh data ke `path` (checkpoint, PITR)."""
        return None

    def restore_from(self, path: str) -> None:
        """Ganti seluruh data dengan snapshot di `path` (PITR)."""
        return None

    def close(self) -> None:
        """Flush dan tutup semua file."""
        return None
