"""Driver Query Optimizer (QO-16): menjalankan QO sendirian tanpa QP, CCM, dan FRM.

Contoh:
    python -m drivers.qo_driver                      # query contoh bawaan
    python -m drivers.qo_driver --sql "SELECT * FROM student;"
    python -m drivers.qo_driver --file query.sql     # banyak query dipisah ';'
    python -m drivers.qo_driver -i                   # mode interaktif
    python -m drivers.qo_driver --no-catalog         # katalog contoh tanpa Storage Manager
"""

from __future__ import annotations

import argparse
import copy
import sys
from dataclasses import dataclass, field

from common import config
from common.catalog import ColumnSchema, DataType, ForeignKey, TableSchema, TableStats
from common.errors import MdbmsError, QuerySyntaxError, SemanticError, TableNotFoundError
from common.interfaces import QueryOptimizer
from common.plan import ParsedQuery
from query_optimizer.plan_printer import render_plan

SAMPLE_QUERIES: list[str] = [
    "SELECT * FROM student;",
    "SELECT student.name, enrollment.grade FROM student JOIN enrollment ON student.id = enrollment.student_id WHERE student.gpa > 3.5;",
    "SELECT * FROM student, course WHERE student.id = course.id ORDER BY student.gpa DESC;",
    "UPDATE student SET gpa = gpa + 0.1 WHERE id = 1;",
    "INSERT INTO student VALUES (6, 'Budi', 3.2);",
    "DELETE FROM student WHERE id = 6;",
    "CREATE INDEX idx_gpa ON student(gpa) USING BTREE;",
    "BEGIN TRANSACTION;",
    "COMMIT;",
    "EXPLAIN SELECT * FROM student WHERE id = 1;",
    "SELEC * FROM student;",
]


@dataclass
class CaseResult:
    """Hasil satu query di driver."""

    sql: str
    ok: bool
    output: str


@dataclass
class DriverReport:
    """Ringkasan seluruh query yang dijalankan driver."""

    results: list[CaseResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def ok(self) -> int:
        return sum(1 for r in self.results if r.ok)

    @property
    def failed(self) -> int:
        return self.total - self.ok


class QODriver:
    """Menjalankan parse, optimasi, biaya, dan tampilan plan lewat facade QueryOptimizer."""

    def __init__(self, qo: QueryOptimizer) -> None:
        self._qo = qo

    def run_query(self, sql: str) -> CaseResult:
        """Proses satu query dan kembalikan teks laporannya. Error QO tidak dilempar keluar."""
        baris = [f"SQL      : {sql}"]
        try:
            parsed = self._qo.parse_query(sql)
            baris.append(f"Jenis    : {self._describe_kind(parsed)}")
            if parsed.plan is None:
                return CaseResult(sql, True, "\n".join(baris))

            optimized = self._qo.optimize_query(copy.deepcopy(parsed))
            baris.append(f"Sebelum  : biaya {self._qo.get_cost(parsed.plan):g} blok")
            baris.append(self._indent(render_plan(parsed.plan)))
            baris.append(f"Sesudah  : biaya {self._qo.get_cost(optimized.plan):g} blok"
                         if optimized.plan is not None else "Sesudah  : (tidak ada plan)")
            if optimized.plan is not None:
                baris.append(self._indent(render_plan(optimized.plan)))
            return CaseResult(sql, True, "\n".join(baris))
        except MdbmsError as e:
            baris.append(f"Error    : {self._describe_error(e)}")
            return CaseResult(sql, False, "\n".join(baris))
        except Exception as e:
            baris.append(f"Error    : kesalahan internal optimizer: {type(e).__name__}: {e}")
            return CaseResult(sql, False, "\n".join(baris))

    def run_queries(self, queries: list[str]) -> DriverReport:
        """Jalankan banyak query berurutan dan cetak hasil tiap query."""
        report = DriverReport()
        for i, sql in enumerate(queries, start=1):
            result = self.run_query(sql)
            report.results.append(result)
            print(f"[{i}] {'OK' if result.ok else 'GAGAL'}")
            print(result.output)
            print()
        return report

    def run_file(self, path: str) -> DriverReport:
        """Baca file SQL, pecah per ';', lalu jalankan semuanya."""
        with open(path, encoding="utf-8") as f:
            return self.run_queries(split_queries(f.read()))

    def interactive(self) -> None:
        """REPL sederhana. Query diakhiri ';', keluar dengan 'exit' atau Ctrl+D."""
        print("QO driver interaktif. Akhiri query dengan ';', ketik 'exit' untuk keluar.")
        buffer = ""
        while True:
            try:
                line = input("qo> " if not buffer else "... ")
            except EOFError:
                print()
                return
            if not buffer and line.strip().lower() in ("exit", "quit"):
                return
            buffer += line + "\n"
            if ";" in line:
                for sql in split_queries(buffer):
                    print(self.run_query(sql).output)
                    print()
                buffer = ""

    @staticmethod
    def print_summary(report: DriverReport) -> None:
        """Cetak jumlah query yang berhasil dan gagal."""
        print(f"Total {report.total} query: {report.ok} berhasil, {report.failed} gagal")

    @staticmethod
    def _describe_kind(parsed: ParsedQuery) -> str:
        if parsed.command is not None:
            return f"{parsed.kind.name} ({parsed.command.name})"
        return parsed.kind.name

    @staticmethod
    def _describe_error(e: MdbmsError) -> str:
        match e:
            case QuerySyntaxError():
                return f"sintaks: {e.message} (posisi {e.position}, token {e.token!r})"
            case SemanticError():
                return f"semantik: {e.message} (objek {e.object_name!r})"
            case _:
                return e.message

    @staticmethod
    def _indent(text: str) -> str:
        return "\n".join("    " + b for b in text.splitlines())


def split_queries(text: str) -> list[str]:
    """Pecah teks jadi query per ';'. Bagian kosong dibuang. Belum menangani ';' di dalam string."""
    return [q.strip() + ";" for q in text.split(";") if q.strip()]


class SampleCatalog:
    """Katalog dan statistik contoh in-memory supaya driver bisa jalan tanpa SM.

    Hanya menyediakan method yang dipakai QO: list_tables, get_schema, get_stats.
    Skema mengikuti SAMPLE_QUERIES (student, course, enrollment).
    """

    def __init__(self) -> None:
        def col(name: str, kind: DataType, length: int | None = None, nullable: bool = True) -> ColumnSchema:
            return ColumnSchema(name, kind, length, nullable)

        self._schemas = {
            "student": TableSchema("student", [
                col("id", DataType.INTEGER, nullable=False), col("name", DataType.VARCHAR, 50),
                col("gpa", DataType.FLOAT),
            ], primary_key=["id"]),
            "course": TableSchema("course", [
                col("id", DataType.INTEGER, nullable=False), col("name", DataType.VARCHAR, 60),
                col("credits", DataType.INTEGER),
            ], primary_key=["id"]),
            "enrollment": TableSchema("enrollment", [
                col("student_id", DataType.INTEGER, nullable=False),
                col("course_id", DataType.INTEGER, nullable=False), col("grade", DataType.FLOAT),
            ], primary_key=["student_id", "course_id"], foreign_keys=[
                ForeignKey("student_id", "student", "id"), ForeignKey("course_id", "course", "id"),
            ]),
        }
        self._stats = {
            "student": TableStats("student", 1000, 25, 62.0, 40, {"id": 1000, "name": 990, "gpa": 200}),
            "course": TableStats("course", 50, 2, 72.0, 30, {"id": 50, "name": 50, "credits": 3}),
            "enrollment": TableStats("enrollment", 5000, 30, 16.0, 170,
                                     {"student_id": 1000, "course_id": 50, "grade": 40}),
        }

    def list_tables(self) -> list[str]:
        return list(self._schemas)

    def get_schema(self, table: str) -> TableSchema:
        if table not in self._schemas:
            raise TableNotFoundError(f"Tabel tidak ditemukan: {table}", table)
        return self._schemas[table]

    def get_stats(self, table: str) -> TableStats:
        self.get_schema(table)
        return self._stats[table]


def build_optimizer(data_dir: str | None) -> QueryOptimizer:
    """Buat QueryOptimizerFacade. Dengan data_dir, katalog diambil dari Storage Manager asli."""
    from query_optimizer import QueryOptimizerFacade

    if data_dir is None:
        return QueryOptimizerFacade(SampleCatalog())
    from storage_manager.facade import StorageManager

    return QueryOptimizerFacade(StorageManager(data_dir))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Driver Query Optimizer")
    sumber = parser.add_mutually_exclusive_group()
    sumber.add_argument("--sql", help="jalankan satu query")
    sumber.add_argument("--file", help="jalankan query dari file, dipisah ';'")
    sumber.add_argument("-i", "--interactive", action="store_true", help="mode interaktif")
    parser.add_argument("--data-dir", default=config.DATA_DIR, help="direktori data Storage Manager")
    parser.add_argument("--no-catalog", action="store_true", help="pakai katalog contoh in-memory, tanpa Storage Manager")
    args = parser.parse_args(argv)

    driver = QODriver(build_optimizer(None if args.no_catalog else args.data_dir))
    if args.interactive:
        driver.interactive()
        return 0
    if args.sql:
        report = driver.run_queries([args.sql])
    elif args.file:
        report = driver.run_file(args.file)
    else:
        # query contoh sengaja berisi query salah, jadi exit code tidak dipakai di sini
        driver.print_summary(driver.run_queries(SAMPLE_QUERIES))
        return 0
    driver.print_summary(report)
    return 0 if report.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
