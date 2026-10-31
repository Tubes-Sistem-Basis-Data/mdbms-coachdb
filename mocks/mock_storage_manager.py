"""Mock Storage Manager: in-memory dengan tiga tabel contoh."""

from __future__ import annotations

import json
import math
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

from common import config
from common.catalog import (
    ColumnSchema,
    DataType,
    ForeignKey,
    IndexInfo,
    TableSchema,
    TableStats,
)
from common.errors import SemanticError, StorageError
from common.interfaces import StorageManager
from common.storage import RID, Row

SLOTS_PER_PAGE = 4

_TYPE_SIZE = {DataType.INTEGER: 4, DataType.FLOAT: 8}


def _sample_schemas() -> list[TableSchema]:
    return [
        TableSchema(
            name="student",
            columns=[
                ColumnSchema("id", DataType.INTEGER, nullable=False),
                ColumnSchema("name", DataType.VARCHAR, 50),
                ColumnSchema("gpa", DataType.FLOAT),
                ColumnSchema("gender", DataType.CHAR, 1),
            ],
            primary_key=["id"],
        ),
        TableSchema(
            name="course",
            columns=[
                ColumnSchema("id", DataType.INTEGER, nullable=False),
                ColumnSchema("title", DataType.VARCHAR, 50),
                ColumnSchema("credits", DataType.INTEGER),
                ColumnSchema("dept", DataType.CHAR, 3),
            ],
            primary_key=["id"],
        ),
        TableSchema(
            name="enrollment",
            columns=[
                ColumnSchema("student_id", DataType.INTEGER, nullable=False),
                ColumnSchema("course_id", DataType.INTEGER, nullable=False),
                ColumnSchema("grade", DataType.CHAR, 2),
                ColumnSchema("score", DataType.FLOAT),
            ],
            primary_key=["student_id", "course_id"],
            foreign_keys=[
                ForeignKey("student_id", "student", "id"),
                ForeignKey("course_id", "course", "id"),
            ],
        ),
    ]


_SAMPLE_ROWS: dict[str, list[list]] = {
    "student": [
        [1, "Andi", 3.75, "L"],
        [2, "Budi", 3.20, "L"],
        [3, "Citra", 3.90, "P"],
        [4, "Dewi", 2.85, "P"],
        [5, "Eko", 3.50, "L"],
    ],
    "course": [
        [101, "Sistem Basis Data", 3, "IF"],
        [102, "Struktur Data", 3, "IF"],
        [103, "Kalkulus", 4, "MA"],
        [104, "Fisika Dasar", 4, "FI"],
        [105, "Probabilitas", 2, "MA"],
    ],
    "enrollment": [
        [1, 101, "A", 88.5],
        [2, 101, "B", 75.0],
        [3, 102, "A", 92.0],
        [4, 103, "C", 61.5],
        [5, 104, "AB", 81.0],
    ],
}


class MockStorageManager(StorageManager):
    """SM in-memory untuk pengembangan komponen lain. Data hilang saat proses berhenti."""

    def __init__(self, data_dir: str | None = None) -> None:
        self.data_dir = data_dir or config.DATA_DIR
        self._lock = threading.RLock()
        self._schemas: dict[str, TableSchema] = {}
        self._rows: dict[str, dict[RID, list]] = {}
        self._next_slot: dict[str, int] = {}
        self._reserved: set[RID] = set()
        self._page_lsn: dict[tuple[str, int], int] = {}
        self._log_flusher: Callable[[int], bool] | None = None
        for schema in _sample_schemas():
            self._schemas[schema.name] = schema
            self._rows[schema.name] = {}
            self._next_slot[schema.name] = 0
            for values in _SAMPLE_ROWS[schema.name]:
                rid = self.allocate_rid(schema.name)
                self._reserved.discard(rid)
                self._rows[schema.name][rid] = list(values)

    def _check_table(self, table: str) -> TableSchema:
        schema = self._schemas.get(table)
        if schema is None:
            raise SemanticError(f"tabel '{table}' tidak ada", object_name=table)
        return schema

    def _touch(self, table: str, rid: RID, lsn: int | None) -> None:
        if lsn is None:
            return
        key = (table, rid.page_id)
        self._page_lsn[key] = max(self._page_lsn.get(key, -1), lsn)

    def list_tables(self) -> list[str]:
        """Daftar nama tabel."""
        return list(self._schemas)

    def get_schema(self, table: str) -> TableSchema:
        """Skema tabel."""
        return self._check_table(table)

    def get_stats(self, table: str) -> TableStats:
        """Statistik dihitung langsung dari data in-memory."""
        schema = self._check_table(table)
        with self._lock:
            rows = list(self._rows[table].values())
        l_r = float(sum(_TYPE_SIZE.get(c.type, c.length or 1) for c in schema.columns))
        f_r = max(1, int(config.BLOCK_SIZE // l_r))
        n_r = len(rows)
        distinct = {
            c.name: len({r[i] for r in rows}) for i, c in enumerate(schema.columns)
        }
        return TableStats(table, n_r, math.ceil(n_r / f_r), l_r, f_r, distinct)

    def scan(self, table: str) -> Iterator[Row]:
        """Iterasi snapshot baris tabel sesuai urutan RID."""
        self._check_table(table)
        with self._lock:
            items = sorted(self._rows[table].items(), key=lambda kv: (kv[0].page_id, kv[0].slot))
        for rid, values in items:
            yield Row(rid, list(values))

    def fetch(self, table: str, rid: RID) -> Row | None:
        """Ambil satu baris, None jika tidak ada."""
        self._check_table(table)
        with self._lock:
            values = self._rows[table].get(rid)
        return None if values is None else Row(rid, list(values))

    def allocate_rid(self, table: str) -> RID:
        """RID berurutan: slot ke-n ada di page n // SLOTS_PER_PAGE."""
        self._check_table(table)
        with self._lock:
            n = self._next_slot[table]
            self._next_slot[table] = n + 1
            rid = RID(n // SLOTS_PER_PAGE, n % SLOTS_PER_PAGE)
            self._reserved.add(rid)
        return rid

    def release_rid(self, rid: RID) -> None:
        """Lepas RID yang dipesan. Slot tidak dipakai ulang di mock."""
        with self._lock:
            self._reserved.discard(rid)

    def insert(self, table: str, rid: RID, values: list, lsn: int | None) -> None:
        """Tulis baris di RID. Idempoten supaya aman untuk undo/redo."""
        schema = self._check_table(table)
        if len(values) != len(schema.columns):
            raise StorageError(f"jumlah nilai {len(values)} tidak sesuai kolom {table}")
        with self._lock:
            self._reserved.discard(rid)
            self._rows[table][rid] = list(values)
            self._touch(table, rid, lsn)

    def update(self, table: str, rid: RID, values: list, lsn: int | None) -> None:
        """Ganti nilai baris. Raise StorageError jika baris tidak ada."""
        schema = self._check_table(table)
        if len(values) != len(schema.columns):
            raise StorageError(f"jumlah nilai {len(values)} tidak sesuai kolom {table}")
        with self._lock:
            if rid not in self._rows[table]:
                raise StorageError(f"baris {rid} tidak ada")
            self._rows[table][rid] = list(values)
            self._touch(table, rid, lsn)

    def delete(self, table: str, rid: RID, lsn: int | None) -> None:
        """Hapus baris jika ada."""
        self._check_table(table)
        with self._lock:
            self._rows[table].pop(rid, None)
            self._touch(table, rid, lsn)

    def delete_all(self, table: str, lsn: int | None) -> None:
        """Kosongkan tabel."""
        self._check_table(table)
        with self._lock:
            for rid in list(self._rows[table]):
                self._touch(table, rid, lsn)
            self._rows[table].clear()

    def create_index(self, info: IndexInfo) -> None:
        """Hanya mencatat IndexInfo di skema."""
        schema = self._check_table(info.table)
        if info.column not in schema.column_names():
            raise SemanticError(f"kolom '{info.column}' tidak ada", object_name=info.column)
        with self._lock:
            if any(i.name == info.name for i in schema.indexes):
                raise SemanticError(f"index '{info.name}' sudah ada", object_name=info.name)
            schema.indexes.append(info)

    def index_lookup(self, table: str, column: str, key) -> list[RID]:
        """Pencarian linear nilai sama dengan key."""
        idx = self._check_table(table).column_index(column)
        return [row.rid for row in self.scan(table) if row.values[idx] == key]

    def index_range(self, table: str, column: str, low, high) -> list[RID]:
        """Pencarian linear low <= nilai <= high, NULL dilewati."""
        idx = self._check_table(table).column_index(column)
        result = []
        for row in self.scan(table):
            v = row.values[idx]
            if v is None:
                continue
            if (low is None or v >= low) and (high is None or v <= high):
                result.append(row.rid)
        return result

    def flush(self, page_ids: list[int] | None = None) -> None:
        """Tidak menulis disk; hanya memanggil log flusher dengan page_lsn terbesar."""
        with self._lock:
            lsns = [
                lsn for (_, pid), lsn in self._page_lsn.items()
                if page_ids is None or pid in page_ids
            ]
        if (
            self._log_flusher is not None
            and lsns
            and self._log_flusher(max(lsns)) is False
        ):
            raise StorageError("log belum permanen, page tidak boleh ditulis")

    def set_log_flusher(self, fn: Callable[[int], bool]) -> None:
        """Pasang callback flush_log dari FRM."""
        self._log_flusher = fn

    def attach_wal(self, frm) -> None:
        """Hubungkan SM ke FRM lewat callback flush log."""
        self._log_flusher = frm.flush_log_until

    def undo_insert(self, table: str, rid: RID) -> None:
        """Undo INSERT: hapus baris jika ada."""
        self.delete(table, rid, None)

    def undo_update(self, table: str, rid: RID, before) -> None:
        """Undo UPDATE: tulis before-image."""
        with self._lock:
            if rid in self._rows[table]:
                self._rows[table][rid] = list(before)
            else:
                self._rows[table][rid] = list(before)

    def undo_delete(self, table: str, rid: RID, before) -> None:
        """Undo DELETE: sisipkan before-image jika belum ada."""
        with self._lock:
            if rid not in self._rows[table]:
                self._rows[table][rid] = list(before)

    def redo_insert(self, table: str, rid: RID, after) -> None:
        """Redo INSERT: tulis after-image."""
        with self._lock:
            self._rows[table][rid] = list(after)

    def redo_update(self, table: str, rid: RID, after) -> None:
        """Redo UPDATE: tulis after-image."""
        with self._lock:
            self._rows[table][rid] = list(after)

    def redo_delete(self, table: str, rid: RID) -> None:
        """Redo DELETE: hapus baris."""
        with self._lock:
            self._rows[table].pop(rid, None)

    def flush_all(self) -> None:
        """Tulis semua page dirty."""
        self.flush(None)

    def snapshot_to(self, path: str) -> None:
        """Simpan snapshot isi tabel ke file JSON."""
        with self._lock:
            payload = {
                table: [
                    [rid.page_id, rid.slot, list(values)]
                    for rid, values in rows.items()
                ]
                for table, rows in self._rows.items()
            }
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle)

    def restore_from(self, path: str) -> None:
        """Pulihkan isi tabel dari snapshot JSON."""
        with Path(path).open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        with self._lock:
            for table, rows in payload.items():
                self._check_table(table)
                self._rows[table] = {
                    RID(int(page), int(slot)): list(values)
                    for page, slot, values in rows
                }
                max_slot = max(
                    (rid.page_id * SLOTS_PER_PAGE + rid.slot for rid in self._rows[table]),
                    default=-1,
                )
                self._next_slot[table] = max_slot + 1

    def rebuild_indexes(self) -> None:
        """Tidak ada index fisik di mock."""
        return
