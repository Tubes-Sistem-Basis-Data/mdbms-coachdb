"""Driver Storage Manager (SM-15).

Menjalankan skenario uji komponen Storage Manager lewat kontrak
``common.interfaces.StorageManager``. Driver bisa dipakai terhadap
``MockStorageManager`` maupun implementasi asli.
"""

from __future__ import annotations

import argparse
import tempfile
import uuid
from dataclasses import dataclass
from typing import Callable

from common.catalog import ColumnSchema, DataType, IndexInfo
from common.errors import MdbmsError
from common.interfaces import StorageManager
from common.storage import RID, Row


def _seed_sample(sm: StorageManager) -> None:
    """Isi tabel contoh driver. Facade DDL (create_table) masih stub."""
    existing = set(sm.list_tables())
    if "student" not in existing:
        sm._catalog.create_table(
            "student",
            (
                ColumnSchema("id", DataType.INTEGER, nullable=False),
                ColumnSchema("name", DataType.VARCHAR, 50),
                ColumnSchema("gpa", DataType.FLOAT),
            ),
            ("id",),
            (),
        )
        sm.bulk_load(
            "student",
            [[1, "Andi", 3.75], [2, "Budi", 3.20], [3, "Citra", 3.90]],
        )
    if "enrollment" not in existing:
        sm._catalog.create_table(
            "enrollment",
            (
                ColumnSchema("student_id", DataType.INTEGER, nullable=False),
                ColumnSchema("course_id", DataType.INTEGER, nullable=False),
            ),
            ("student_id", "course_id"),
            (),
        )
        sm.bulk_load("enrollment", [[1, 101], [2, 101]])


@dataclass
class ScenarioResult:
    """Hasil satu skenario."""

    name: str
    passed: bool
    message: str = ""


class SMDriver:
    """Jalankan dan laporkan skenario komponen Storage Manager."""

    def __init__(
        self,
        sm: StorageManager | None = None,
        data_dir: str | None = None,
        out: Callable[[str], None] | None = None,
    ) -> None:
        self._sm = sm
        self._owns_sm = False
        self._data_dir = data_dir
        self._out = out or print
        self._results: list[ScenarioResult] = []
        self._tmp_dir: tempfile.TemporaryDirectory | None = None

    # ------------------------------------------------------------------ publik

    def run_all(self) -> None:
        """Jalankan semua skenario yang terdaftar."""
        self._results = []
        for name in self._scenario_methods():
            self.run_scenario(name)

    def run_scenario(self, name: str) -> bool:
        """Jalankan satu skenario. Nama asing menghasilkan ``False``."""
        method = self._scenario_methods().get(name)
        if method is None:
            self._results.append(
                ScenarioResult(name, False, "skenario tidak dikenal")
            )
            return False
        try:
            method(self._storage())
        except Exception as exc:  # driver tidak boleh melempar ke pemanggil
            self._results.append(
                ScenarioResult(name, False, f"{type(exc).__name__}: {exc}")
            )
            return False
        self._results.append(ScenarioResult(name, True))
        return True

    def print_summary(self) -> None:
        """Cetak tabel LULUS/GAGAL per skenario."""
        for result in self._results:
            status = "LULUS" if result.passed else "GAGAL"
            line = f"[{status}] {result.name}"
            if result.message:
                line += f" - {result.message}"
            self._out(line)
        passed = sum(result.passed for result in self._results)
        self._out(f"{passed}/{len(self._results)} skenario LULUS")

    def scenario_names(self) -> list[str]:
        """Daftar nama skenario sesuai urutan eksekusi."""
        return list(self._scenario_methods())

    def close(self) -> None:
        """Tutup SM milik driver, lalu bersihkan direktori sementara."""
        if self._owns_sm and self._sm is not None:
            close = getattr(self._sm, "close", None)
            if callable(close):
                close()
            self._sm = None
            self._owns_sm = False
        if self._tmp_dir is not None:
            self._tmp_dir.cleanup()
            self._tmp_dir = None

    # ---------------------------------------------------------------- internal

    def _storage(self) -> StorageManager:
        if self._sm is None:
            self._sm = self._build_storage()
        return self._sm

    def _build_storage(self) -> StorageManager:
        try:
            from mocks.mock_storage_manager import MockStorageManager
        except ImportError:
            pass
        else:
            return MockStorageManager(self._data_dir)

        from storage_manager.facade import StorageManager as RealStorageManager

        if self._data_dir is None:
            self._tmp_dir = tempfile.TemporaryDirectory(prefix="coachdb-sm-")
            self._data_dir = self._tmp_dir.name
        sm = RealStorageManager(self._data_dir)
        self._owns_sm = True
        _seed_sample(sm)
        return sm

    def _scenario_methods(self) -> dict[str, Callable[[StorageManager], None]]:
        return {
            "list_tables_and_schema": self._list_tables_and_schema,
            "scan_order_and_shape": self._scan_order_and_shape,
            "fetch_hit": self._fetch_hit,
            "fetch_miss": self._fetch_miss,
            "allocate_and_release_rid": self._allocate_and_release_rid,
            "insert": self._insert,
            "insert_idempotent": self._insert_idempotent,
            "update_idempotent": self._update_idempotent,
            "delete_then_fetch_none": self._delete_then_fetch_none,
            "delete_all_and_restore": self._delete_all_and_restore,
            "stats": self._stats,
            "create_index_duplicate": self._create_index_duplicate,
            "index_lookup": self._index_lookup,
            "index_range_inclusive": self._index_range_inclusive,
            "missing_table_error": self._missing_table_error,
            "flush_and_log": self._flush_and_log,
        }

    # ---------------------------------------------------------------- bantuan

    @staticmethod
    def _rows(sm: StorageManager, table: str) -> list[Row]:
        return list(sm.scan(table))

    @staticmethod
    def _first_row(sm: StorageManager, table: str) -> Row:
        rows = list(sm.scan(table))
        if not rows:
            raise AssertionError(f"tabel {table} kosong")
        return rows[0]

    @staticmethod
    def _next_key(sm: StorageManager, table: str, column: str) -> int:
        schema = sm.get_schema(table)
        position = schema.column_index(column)
        values = [
            row.values[position]
            for row in sm.scan(table)
            if row.values[position] is not None
        ]
        return max(values) + 1 if values else 1

    @staticmethod
    def _fresh_values(sm: StorageManager, table: str) -> list:
        schema = sm.get_schema(table)
        values = [None] * len(schema.columns)
        for position, column in enumerate(schema.columns):
            if column.name in schema.primary_key:
                values[position] = SMDriver._next_key(sm, table, column.name)
                break
        return values

    @staticmethod
    def _changed_values(sm: StorageManager, table: str, row: Row) -> list:
        schema = sm.get_schema(table)
        values = list(row.values)
        for position, column in enumerate(schema.columns):
            if column.name in schema.primary_key:
                continue
            if column.type is DataType.INTEGER:
                values[position] = 999999
            elif column.type is DataType.FLOAT:
                values[position] = 9.99
            elif column.type is DataType.CHAR:
                values[position] = "U" if column.length == 1 else "UP"
            else:
                values[position] = "updated"
            break
        return values

    @staticmethod
    def _insert_fresh(sm: StorageManager, table: str, lsn: int) -> RID:
        rid = sm.allocate_rid(table)
        sm.insert(table, rid, SMDriver._fresh_values(sm, table), lsn)
        return rid

    @staticmethod
    def _ensure_index(sm: StorageManager, table: str, column: str) -> None:
        if any(info.column == column for info in sm.list_indexes(table)):
            return
        sm.create_index(
            IndexInfo(
                name=f"idx_driver_{uuid.uuid4().hex}",
                table=table,
                column=column,
            )
        )

    # --------------------------------------------------------------- skenario

    def _list_tables_and_schema(self, sm: StorageManager) -> None:
        tables = sm.list_tables()
        if "student" not in tables:
            raise AssertionError("tabel contoh 'student' tidak ada")
        schema = sm.get_schema("student")
        if schema.name != "student":
            raise AssertionError(f"nama schema salah: {schema.name}")
        if not schema.columns:
            raise AssertionError("schema tidak punya kolom")
        if "id" not in schema.column_names():
            raise AssertionError("kolom 'id' tidak ada")

    def _scan_order_and_shape(self, sm: StorageManager) -> None:
        rows = self._rows(sm, "student")
        if not rows:
            raise AssertionError("scan tabel student kosong")
        schema = sm.get_schema("student")
        keys = [(row.rid.page_id, row.rid.slot) for row in rows]
        if keys != sorted(keys):
            raise AssertionError("hasil scan tidak urut (page_id, slot)")
        for row in rows:
            if not isinstance(row, Row):
                raise AssertionError(f"baris bukan Row: {type(row)!r}")
            if len(row.values) != len(schema.columns):
                raise AssertionError("jumlah nilai tidak sesuai jumlah kolom")

    def _fetch_hit(self, sm: StorageManager) -> None:
        row = self._first_row(sm, "student")
        found = sm.fetch("student", row.rid)
        if found is None or found.rid != row.rid or found.values != row.values:
            raise AssertionError(f"fetch tidak mengembalikan baris {row.rid}")

    def _fetch_miss(self, sm: StorageManager) -> None:
        missing = RID("student", 999999, 0)
        if sm.fetch("student", missing) is not None:
            raise AssertionError("fetch RID tidak ada harus None")

    def _allocate_and_release_rid(self, sm: StorageManager) -> None:
        before = self._rows(sm, "student")
        first = sm.allocate_rid("student")
        second = sm.allocate_rid("student")
        if not isinstance(first, RID) or first == second:
            raise AssertionError("allocate_rid harus memberi RID unik")
        sm.release_rid(first)
        sm.release_rid(second)
        if self._rows(sm, "student") != before:
            raise AssertionError("release_rid mengubah isi tabel")

    def _insert(self, sm: StorageManager) -> None:
        table = "student"
        rid = sm.allocate_rid(table)
        values = self._fresh_values(sm, table)
        sm.insert(table, rid, values, 1)
        found = sm.fetch(table, rid)
        if found is None or found.values != values:
            raise AssertionError("insert tidak menulis baris di RID yang dipesan")

    def _insert_idempotent(self, sm: StorageManager) -> None:
        table = "student"
        rid = self._insert_fresh(sm, table, 1)
        row = sm.fetch(table, rid)
        assert row is not None
        sm.insert(table, rid, row.values, None)
        matches = [item for item in sm.scan(table) if item.rid == rid]
        if len(matches) != 1:
            raise AssertionError("insert ulang membuat baris ganda")

    def _update_idempotent(self, sm: StorageManager) -> None:
        table = "student"
        row = self._first_row(sm, table)
        values = self._changed_values(sm, table, row)
        sm.update(table, row.rid, values, 1)
        sm.update(table, row.rid, values, 2)
        found = sm.fetch(table, row.rid)
        if found is None or found.rid != row.rid or found.values != values:
            raise AssertionError("update tidak idempoten")

    def _delete_then_fetch_none(self, sm: StorageManager) -> None:
        table = "student"
        rid = self._insert_fresh(sm, table, 1)
        before = len(self._rows(sm, table))
        sm.delete(table, rid, 2)
        if sm.fetch(table, rid) is not None:
            raise AssertionError("fetch setelah delete harus None")
        sm.delete(table, rid, None)
        if len(self._rows(sm, table)) != before - 1:
            raise AssertionError("delete ganda mengubah jumlah baris")

    def _delete_all_and_restore(self, sm: StorageManager) -> None:
        tables = sm.list_tables()
        table = "enrollment" if "enrollment" in tables else "student"
        rows = self._rows(sm, table)
        if not rows:
            raise AssertionError(f"tabel {table} kosong")
        sm.delete_all(table, 1)
        if self._rows(sm, table):
            raise AssertionError("delete_all menyisakan baris")
        for row in rows:
            sm.insert(table, row.rid, row.values, None)
        for row in rows:
            found = sm.fetch(table, row.rid)
            if found is None or found.values != row.values:
                raise AssertionError("insert ulang tidak memulihkan baris")

    def _stats(self, sm: StorageManager) -> None:
        stats = sm.get_stats("student")
        if stats.table != "student":
            raise AssertionError("nama tabel pada statistik salah")
        if stats.n_r < 0 or stats.b_r < 0 or stats.l_r < 0 or stats.f_r < 0:
            raise AssertionError("statistik tidak boleh negatif")
        if not isinstance(stats.distinct, dict):
            raise AssertionError("distinct harus dict")

    def _create_index_duplicate(self, sm: StorageManager) -> None:
        table = "student"
        schema = sm.get_schema(table)
        indexed = {info.column for info in sm.list_indexes(table)}
        column = next(
            (item.name for item in schema.columns if item.name not in indexed),
            schema.columns[0].name,
        )
        name = f"idx_driver_{uuid.uuid4().hex}"
        info = IndexInfo(name=name, table=table, column=column)
        sm.create_index(info)
        try:
            sm.create_index(info)
        except MdbmsError:
            pass
        else:
            raise AssertionError("index dengan nama ganda harus ditolak")
        try:
            sm.create_index(
                IndexInfo(name=f"{name}_bad", table=table, column="__missing__")
            )
        except MdbmsError:
            pass
        else:
            raise AssertionError("index pada kolom tidak ada harus ditolak")

    def _index_lookup(self, sm: StorageManager) -> None:
        table = "student"
        row = self._first_row(sm, table)
        schema = sm.get_schema(table)
        column = schema.columns[0].name
        self._ensure_index(sm, table, column)
        position = schema.column_index(column)
        key = row.values[position]
        rids = sm.index_lookup(table, column, key)
        if row.rid not in rids:
            raise AssertionError("index_lookup tidak menemukan baris yang ada")
        for rid in rids:
            found = sm.fetch(table, rid)
            if found is None or found.values[position] != key:
                raise AssertionError("index_lookup mengembalikan RID yang salah")

    def _index_range_inclusive(self, sm: StorageManager) -> None:
        table = "student"
        rows = self._rows(sm, table)
        schema = sm.get_schema(table)
        column = schema.columns[0].name
        self._ensure_index(sm, table, column)
        position = schema.column_index(column)
        values = sorted(
            row.values[position]
            for row in rows
            if row.values[position] is not None
        )
        if not values:
            raise AssertionError("tidak ada nilai untuk menguji range")
        low, high = values[0], values[-1]
        expected = {
            row.rid
            for row in rows
            if row.values[position] is not None
            and low <= row.values[position] <= high
        }
        if set(sm.index_range(table, column, low, high)) != expected:
            raise AssertionError("index_range tidak inklusif di kedua ujung")
        middle = values[len(values) // 2]
        got = sm.index_range(table, column, middle, middle)
        for rid in got:
            found = sm.fetch(table, rid)
            if found is None or found.values[position] != middle:
                raise AssertionError("index_range low == high salah")

    def _missing_table_error(self, sm: StorageManager) -> None:
        try:
            sm.get_schema("__tabel_tidak_ada__")
        except MdbmsError:
            return
        raise AssertionError("get_schema tabel tidak ada harus raise MdbmsError")

    def _flush_and_log(self, sm: StorageManager) -> None:
        calls: list[int] = []

        def flusher(lsn: int) -> bool:
            calls.append(lsn)
            return True

        sm.set_log_flusher(flusher)
        self._insert_fresh(sm, "student", 7)
        sm.flush()
        if not calls or max(calls) < 7:
            raise AssertionError("flush tidak memanggil log flusher dengan page_lsn")
        self._insert_fresh(sm, "student", 8)
        sm.set_log_flusher(lambda _lsn: False)
        try:
            sm.flush()
        except MdbmsError:
            pass
        else:
            raise AssertionError("flush harus gagal jika log belum permanen")
        sm.set_log_flusher(lambda _lsn: True)
        sm.flush()


def main(argv: list[str] | None = None) -> int:
    """Entry point ``python -m drivers.sm_driver``."""
    parser = argparse.ArgumentParser(description="Driver komponen Storage Manager")
    parser.add_argument("--scenario", help="jalankan satu skenario saja")
    parser.add_argument("--data-dir", help="direktori data untuk implementasi asli")
    parser.add_argument(
        "--real",
        action="store_true",
        help="pakai StorageManager asli, bukan MockStorageManager",
    )
    args = parser.parse_args(argv)

    tmp_dir: tempfile.TemporaryDirectory | None = None
    sm: StorageManager | None = None
    if args.real:
        from storage_manager.facade import StorageManager as RealStorageManager

        data_dir = args.data_dir
        if data_dir is None:
            tmp_dir = tempfile.TemporaryDirectory(prefix="coachdb-sm-")
            data_dir = tmp_dir.name
        sm = RealStorageManager(data_dir)
        _seed_sample(sm)

    driver = SMDriver(sm=sm, data_dir=args.data_dir)
    try:
        if args.scenario:
            driver.run_scenario(args.scenario)
        else:
            driver.run_all()
        driver.print_summary()
        passed = bool(driver._results) and all(r.passed for r in driver._results)
        return 0 if passed else 1
    finally:
        driver.close()
        if sm is not None:
            sm.close()
        if tmp_dir is not None:
            tmp_dir.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
