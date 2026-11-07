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
        with self._lock:
            return self._catalog.list_tables()

    def get_schema(self, table: str) -> TableSchema:
        """Skema tabel. Raise SemanticError jika tabel tidak ada. Dipanggil QO."""
        with self._lock:
            return self._catalog.get_table_schema(table)

    def get_stats(self, table: str) -> TableStats:
        """Statistik tabel untuk cost model. Dipanggil QO."""
        with self._lock:
            return self._stats.get_stats(table)

    def scan(self, table: str) -> Iterator[Row]:
        """Iterasi seluruh baris tabel. Dipanggil QP."""
        return self._records.scan(table)

    def fetch(self, table: str, rid: RID) -> Row | None:
        """Ambil satu baris lewat RID, None jika tidak ada. Dipanggil QP dan FRM."""
        return self._records.fetch(table, rid)

    def allocate_rid(self, table: str) -> RID:
        """Pesan slot kosong untuk INSERT. Dipanggil QP sebelum minta izin CCM."""
        return self._records.allocate_rid(table)

    def release_rid(self, rid: RID) -> None:
        """Lepas RID yang batal dipakai karena INSERT tidak diizinkan. Dipanggil QP."""
        self._records.release_rid(rid)

    def insert(self, table: str, rid: RID, values: list, lsn: int | None) -> None:
        """Tulis baris di RID. `lsn=None` = upsert undo/redo dari FRM (D9)."""
        self._records.insert(table, rid, tuple(values), lsn)

    def update(self, table: str, rid: RID, values: list, lsn: int | None) -> None:
        """Ganti seluruh nilai baris di RID. `lsn=None` = upsert undo/redo (D9)."""
        self._records.update(table, rid, tuple(values), lsn)

    def delete(self, table: str, rid: RID, lsn: int | None) -> None:
        """Hapus baris di RID (tombstone). `lsn=None` = undo/redo dari FRM (D9)."""
        self._records.delete(table, rid, lsn)

    def delete_all(self, table: str, lsn: int | None) -> None:
        """Hapus seluruh baris tabel. Dipanggil QP untuk DELETE tanpa WHERE."""
        self._records.delete_all(table, lsn)

    def validate(self, table: str, values: list, rid: RID | None = None) -> list:
        """Cek nilai sesuai skema, raise SchemaViolationError jika tidak (D6)."""
        return list(self._records.validate(table, tuple(values), rid))

    def create_table(self, schema: TableSchema) -> None:
        """Buat tabel baru dan catat di katalog. Dipanggil QP untuk CREATE TABLE."""
        return None

    def drop_table(self, table: str) -> None:
        """Hapus tabel beserta index-nya. Dipanggil QP untuk DROP TABLE."""
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
        with self._lock:
            return self._catalog.list_indexes(table)


    def bulk_load(self, table: str, rows: list[list]) -> int:
        """Isi banyak baris sekaligus tanpa log. Dipakai seeder."""
        for row in rows:
            rid = self._records.allocate_rid(table)
            self._records.insert(table, rid, tuple(row), None)
        return len(rows)

    def rebuild_indexes(self) -> None:
        """Bangun ulang index dan statistik dari isi tabel setelah recovery."""
        with self._lock:
            self._index_manager.rebuild_all()
            for table in self._catalog.list_tables():
                self._stats.recount(table)

    def snapshot_to(self, path: str) -> None:
        """Salin seluruh data ke `path` (checkpoint, PITR)."""
        return None

    def restore_from(self, path: str) -> None:
        """Ganti seluruh data dengan snapshot di `path` (PITR)."""
        return None

    def close(self) -> None:
        """Flush dan tutup semua file."""
        return None
