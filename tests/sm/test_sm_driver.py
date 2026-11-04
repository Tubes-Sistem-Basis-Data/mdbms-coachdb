"""Uji SM-15: driver program komponen Storage Manager."""

from __future__ import annotations

import contextlib
import io
import unittest
from typing import Callable, Iterator
from unittest.mock import patch

from common.catalog import ColumnSchema, DataType, IndexInfo, TableSchema, TableStats
from common.errors import StorageError
from common.storage import RID, Row
from drivers.sm_driver import SMDriver, main
from storage_manager.errors import IndexDefinitionError, TableNotFoundError

SCHEMAS = {
    "student": TableSchema(
        name="student",
        columns=[
            ColumnSchema("id", DataType.INTEGER, nullable=False),
            ColumnSchema("name", DataType.VARCHAR, 50),
            ColumnSchema("gpa", DataType.FLOAT),
        ],
        primary_key=["id"],
    ),
    "enrollment": TableSchema(
        name="enrollment",
        columns=[
            ColumnSchema("student_id", DataType.INTEGER, nullable=False),
            ColumnSchema("course_id", DataType.INTEGER, nullable=False),
        ],
        primary_key=["student_id", "course_id"],
    ),
}

SAMPLE_ROWS = {
    "student": [
        [1, "Andi", 3.75],
        [2, "Budi", 3.20],
        [3, "Citra", 3.90],
    ],
    "enrollment": [
        [1, 101],
        [2, 101],
    ],
}

SLOTS_PER_PAGE = 4


class FakeStorageManager:
    """SM in-memory secukupnya untuk menguji SMDriver."""

    def __init__(self) -> None:
        self._schemas = SCHEMAS
        self._rows: dict[str, dict[RID, list]] = {}
        self._next_slot: dict[str, int] = {}
        self._indexes: dict[str, IndexInfo] = {}
        self._page_lsn: dict[tuple[str, int], int] = {}
        self._log_flusher: Callable[[int], bool] | None = None
        for table, rows in SAMPLE_ROWS.items():
            self._rows[table] = {}
            self._next_slot[table] = 0
            for values in rows:
                rid = self.allocate_rid(table)
                self._rows[table][rid] = list(values)

    def _check_table(self, table: str) -> TableSchema:
        if table not in self._schemas:
            raise TableNotFoundError(f"tabel {table} tidak ada")
        return self._schemas[table]

    def list_tables(self) -> list[str]:
        return list(self._schemas)

    def get_schema(self, table: str) -> TableSchema:
        return self._check_table(table)

    def get_stats(self, table: str) -> TableStats:
        schema = self._check_table(table)
        rows = list(self._rows[table].values())
        distinct = {
            column.name: len({row[i] for row in rows})
            for i, column in enumerate(schema.columns)
        }
        return TableStats(
            table=table,
            n_r=len(rows),
            b_r=1 if rows else 0,
            l_r=float(len(schema.columns) * 4),
            f_r=max(1, 4096 // (len(schema.columns) * 4)),
            distinct=distinct,
        )

    def scan(self, table: str) -> Iterator[Row]:
        self._check_table(table)
        items = sorted(
            self._rows[table].items(), key=lambda item: (item[0].page_id, item[0].slot)
        )
        for rid, values in items:
            yield Row(rid, list(values))

    def fetch(self, table: str, rid: RID) -> Row | None:
        self._check_table(table)
        values = self._rows[table].get(rid)
        return None if values is None else Row(rid, list(values))

    def allocate_rid(self, table: str) -> RID:
        self._check_table(table)
        slot = self._next_slot[table]
        self._next_slot[table] = slot + 1
        return RID(table, slot // SLOTS_PER_PAGE, slot % SLOTS_PER_PAGE)

    def release_rid(self, rid: RID) -> None:
        return None

    def insert(self, table: str, rid: RID, values: list, lsn: int | None) -> None:
        self._check_table(table)
        self._rows[table][rid] = list(values)
        self._touch(rid, lsn)

    def update(self, table: str, rid: RID, values: list, lsn: int | None) -> None:
        self._check_table(table)
        if rid not in self._rows[table]:
            raise StorageError(f"baris {rid} tidak ada")
        self._rows[table][rid] = list(values)
        self._touch(rid, lsn)

    def delete(self, table: str, rid: RID, lsn: int | None) -> None:
        self._check_table(table)
        self._rows[table].pop(rid, None)
        self._touch(rid, lsn)

    def delete_all(self, table: str, lsn: int | None) -> None:
        self._check_table(table)
        self._rows[table].clear()

    def create_index(self, info: IndexInfo) -> None:
        schema = self._check_table(info.table)
        if info.column not in schema.column_names():
            raise IndexDefinitionError(f"kolom {info.column} tidak ada")
        if info.name in self._indexes:
            raise IndexDefinitionError(f"index {info.name} sudah ada")
        self._indexes[info.name] = info

    def index_lookup(self, table: str, column: str, key) -> list[RID]:
        position = self._check_table(table).column_index(column)
        return [
            row.rid
            for row in self.scan(table)
            if row.values[position] == key
        ]

    def index_range(self, table: str, column: str, low, high) -> list[RID]:
        position = self._check_table(table).column_index(column)
        result = []
        for row in self.scan(table):
            value = row.values[position]
            if value is None:
                continue
            if (low is None or value >= low) and (high is None or value <= high):
                result.append(row.rid)
        return result

    def flush(self, page_ids: list[int] | None = None) -> None:
        lsns = [
            lsn
            for (_, page_id), lsn in self._page_lsn.items()
            if page_ids is None or page_id in page_ids
        ]
        if self._log_flusher is not None and lsns:
            if not self._log_flusher(max(lsns)):
                raise StorageError("log belum permanen")

    def set_log_flusher(self, fn: Callable[[int], bool]) -> None:
        self._log_flusher = fn

    def _touch(self, rid: RID, lsn: int | None) -> None:
        if lsn is None:
            return
        key = (rid.table, rid.page_id)
        self._page_lsn[key] = max(self._page_lsn.get(key, -1), lsn)


class TestSMDriver(unittest.TestCase):
    def setUp(self) -> None:
        self.lines: list[str] = []
        self.driver = SMDriver(sm=FakeStorageManager(), out=self.lines.append)

    def test_run_all_menjalankan_semua_skenario(self) -> None:
        self.driver.run_all()
        self.assertEqual(
            [result.name for result in self.driver._results],
            self.driver.scenario_names(),
        )
        failed = [result for result in self.driver._results if not result.passed]
        self.assertEqual(failed, [])

    def test_run_scenario_mengembalikan_status(self) -> None:
        self.assertTrue(self.driver.run_scenario("fetch_miss"))
        self.assertFalse(self.driver.run_scenario("skenario_asing"))

    def test_run_scenario_tidak_dikenal(self) -> None:
        self.assertFalse(self.driver.run_scenario("skenario_asing"))
        self.assertIn("tidak dikenal", self.driver._results[0].message)

    def test_print_summary_menampilkan_hasil(self) -> None:
        self.driver.run_all()
        self.driver.print_summary()
        self.assertIn("[LULUS] fetch_hit", self.lines)
        self.assertIn(
            f"{len(self.driver.scenario_names())}/{len(self.driver.scenario_names())}"
            " skenario LULUS",
            self.lines[-1],
        )

    def test_main_mengembalikan_exit_code(self) -> None:
        stdout = io.StringIO()
        with patch.object(
            SMDriver, "_build_storage", return_value=FakeStorageManager()
        ), contextlib.redirect_stdout(stdout):
            code = main([])
        self.assertEqual(code, 0)
        self.assertIn("skenario LULUS", stdout.getvalue())

    def test_main_scenario_tidak_dikenal_mengembalikan_satu(self) -> None:
        stdout = io.StringIO()
        with patch.object(
            SMDriver, "_build_storage", return_value=FakeStorageManager()
        ), contextlib.redirect_stdout(stdout):
            code = main(["--scenario", "skenario_asing"])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
