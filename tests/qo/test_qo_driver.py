"""Uji driver Query Optimizer (QO-16) memakai QO palsu."""

import contextlib
import io
import os
import tempfile
import unittest

from common.errors import QuerySyntaxError, SemanticError
from common.interfaces import QueryOptimizer
from common.plan import ParsedQuery, PlanNode, Project, QueryKind, TableScan, TransactionCommand
from drivers.qo_driver import QODriver, split_queries


class _FakeQO(QueryOptimizer):
    """QO palsu: perilaku ditentukan dari kata pertama query."""

    def __init__(self) -> None:
        self.optimized: list[ParsedQuery] = []

    def parse_query(self, sql: str) -> ParsedQuery:
        kata = sql.split()[0].rstrip(";").upper()
        match kata:
            case "SELECT":
                return ParsedQuery(sql, QueryKind.SELECT, plan=Project(columns=[], children=[TableScan(table="student")]))
            case "COMMIT":
                return ParsedQuery(sql, QueryKind.TRANSACTION, command=TransactionCommand.COMMIT)
            case "SEMANTIK":
                raise SemanticError("tabel tidak ada", object_name="xyz")
            case "CRASH":
                raise ValueError("bug")
            case _:
                raise QuerySyntaxError("token tidak dikenal", position=0, token=kata)

    def optimize_query(self, query: ParsedQuery) -> ParsedQuery:
        self.optimized.append(query)
        query.plan = TableScan(table="student")
        return query

    def get_cost(self, plan: PlanNode) -> float:
        return 12.0 if isinstance(plan, Project) else 5.0

    def explain(self, query: ParsedQuery) -> str:
        return ""


class TestQODriver(unittest.TestCase):
    def setUp(self) -> None:
        self.qo = _FakeQO()
        self.driver = QODriver(self.qo)

    def test_select_menampilkan_plan_dan_biaya(self) -> None:
        r = self.driver.run_query("SELECT * FROM student;")
        self.assertTrue(r.ok)
        self.assertIn("Jenis    : SELECT", r.output)
        self.assertIn("Sebelum  : biaya 12 blok", r.output)
        self.assertIn("    Project [*]\n      TableScan [student]", r.output)
        self.assertIn("Sesudah  : biaya 5 blok", r.output)

    def test_optimasi_memakai_salinan(self) -> None:
        self.driver.run_query("SELECT * FROM student;")
        self.assertEqual(len(self.qo.optimized), 1)
        r = self.driver.run_query("SELECT * FROM student;")
        self.assertIn("Project [*]", r.output)

    def test_transaksi_tanpa_plan(self) -> None:
        r = self.driver.run_query("COMMIT;")
        self.assertTrue(r.ok)
        self.assertIn("TRANSACTION (COMMIT)", r.output)
        self.assertNotIn("Sebelum", r.output)

    def test_error_sintaks(self) -> None:
        r = self.driver.run_query("SELEC * FROM student;")
        self.assertFalse(r.ok)
        self.assertIn("sintaks: token tidak dikenal (posisi 0, token 'SELEC')", r.output)

    def test_error_semantik(self) -> None:
        r = self.driver.run_query("SEMANTIK;")
        self.assertFalse(r.ok)
        self.assertIn("semantik: tabel tidak ada (objek 'xyz')", r.output)

    def test_exception_lain_tidak_bocor(self) -> None:
        r = self.driver.run_query("CRASH;")
        self.assertFalse(r.ok)
        self.assertIn("kesalahan internal optimizer: ValueError: bug", r.output)

    def test_run_queries_dan_ringkasan(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            report = self.driver.run_queries(["SELECT * FROM a;", "COMMIT;", "SALAH;"])
            self.driver.print_summary(report)
        self.assertEqual((report.total, report.ok, report.failed), (3, 2, 1))
        self.assertIn("Total 3 query: 2 berhasil, 1 gagal", out.getvalue())

    def test_run_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "q.sql")
            with open(path, "w", encoding="utf-8") as f:
                f.write("SELECT * FROM a;\n\nCOMMIT;\n")
            with contextlib.redirect_stdout(io.StringIO()):
                report = self.driver.run_file(path)
        self.assertEqual(report.total, 2)
        self.assertEqual(report.failed, 0)


class TestSplitQueries(unittest.TestCase):
    def test_pecah_dan_buang_kosong(self) -> None:
        self.assertEqual(split_queries("SELECT * FROM a;\n ;  COMMIT ;"), ["SELECT * FROM a;", "COMMIT;"])

    def test_tanpa_titik_koma_terakhir(self) -> None:
        self.assertEqual(split_queries("BEGIN"), ["BEGIN;"])

    def test_kosong(self) -> None:
        self.assertEqual(split_queries("  \n "), [])


if __name__ == "__main__":
    unittest.main()
