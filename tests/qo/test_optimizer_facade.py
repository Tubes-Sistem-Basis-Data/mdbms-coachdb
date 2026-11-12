"""Uji QueryOptimizerFacade: parse, optimasi, biaya, dan penanganan error."""

from __future__ import annotations

import unittest

from common.errors import MdbmsError, QuerySyntaxError, SemanticError
from common.expression import ColumnRef
from common.plan import Delete, Filter, Join, JoinKind, Project, QueryKind, TableScan, TransactionCommand, Update
from drivers.qo_driver import SampleCatalog
from query_optimizer.optimizer import QueryOptimizerFacade


class FacadeCase(unittest.TestCase):
    def setUp(self) -> None:
        self.qo = QueryOptimizerFacade(SampleCatalog())


class TestParse(FacadeCase):
    def test_select_sudah_dibind(self) -> None:
        parsed = self.qo.parse_query("SELECT name FROM student WHERE gpa > 3.5;")
        self.assertEqual(parsed.kind, QueryKind.SELECT)
        self.assertEqual(parsed.sql, "SELECT name FROM student WHERE gpa > 3.5;")
        self.assertEqual(parsed.plan.columns, [ColumnRef("student", "name")])
        self.assertEqual(parsed.plan.children[0].condition.left, ColumnRef("student", "gpa"))
        self.assertGreater(parsed.estimated_cost, 0)

    def test_select_bintang_diisi_kolom(self) -> None:
        parsed = self.qo.parse_query("SELECT * FROM course")
        self.assertEqual([c.column for c in parsed.plan.columns], ["id", "name", "credits"])

    def test_transaksi_tanpa_plan(self) -> None:
        for sql, command in (("BEGIN TRANSACTION;", TransactionCommand.BEGIN),
                             ("COMMIT;", TransactionCommand.COMMIT), ("ABORT;", TransactionCommand.ABORT)):
            with self.subTest(sql=sql):
                parsed = self.qo.parse_query(sql)
                self.assertEqual(parsed.command, command)
                self.assertIsNone(parsed.plan)

    def test_dml_dan_index(self) -> None:
        kasus = {
            "INSERT INTO student VALUES (6, 'Budi', 3.2);": QueryKind.INSERT,
            "UPDATE student SET gpa = gpa + 0.1 WHERE id = 1;": QueryKind.UPDATE,
            "DELETE FROM student WHERE id = 6;": QueryKind.DELETE,
            "CREATE INDEX idx_gpa ON student(gpa);": QueryKind.CREATE_INDEX,
            "EXPLAIN SELECT * FROM student WHERE id = 1;": QueryKind.EXPLAIN,
        }
        for sql, kind in kasus.items():
            with self.subTest(sql=sql):
                self.assertEqual(self.qo.parse_query(sql).kind, kind)

    def test_error_sintaks(self) -> None:
        with self.assertRaises(QuerySyntaxError) as ctx:
            self.qo.parse_query("SELEC * FROM student;")
        self.assertEqual(ctx.exception.token, "SELEC")

    def test_error_semantik(self) -> None:
        with self.assertRaises(SemanticError):
            self.qo.parse_query("SELECT * FROM dosen;")
        with self.assertRaises(SemanticError):
            self.qo.parse_query("SELECT nim FROM student;")

    def test_bukan_teks(self) -> None:
        with self.assertRaises(QuerySyntaxError):
            self.qo.parse_query(None)  # type: ignore[arg-type]

    def test_tanpa_katalog_query_data_ditolak(self) -> None:
        qo = QueryOptimizerFacade(None)
        self.assertIsNone(qo.parse_query("COMMIT;").plan)
        with self.assertRaises(SemanticError):
            qo.parse_query("SELECT * FROM student;")

    def test_exception_lain_dibungkus(self) -> None:
        def rusak(query):
            raise ValueError("bug")

        self.qo._binder.bind = rusak
        with self.assertLogs("query_optimizer.optimizer", level="ERROR"):
            with self.assertRaises(MdbmsError) as ctx:
                self.qo.parse_query("SELECT * FROM student;")
        self.assertNotIsInstance(ctx.exception, (QuerySyntaxError, SemanticError))
        self.assertIn("ValueError: bug", ctx.exception.message)


class TestOptimize(FacadeCase):
    def test_kondisi_antar_tabel_jadi_join(self) -> None:
        parsed = self.qo.parse_query("SELECT student.name FROM student, enrollment "
                                     "WHERE student.id = enrollment.student_id AND student.gpa > 3.5;")
        before = parsed.estimated_cost
        optimized = self.qo.optimize_query(parsed)
        joins = []
        nodes = [optimized.plan]
        while nodes:
            node = nodes.pop()
            if isinstance(node, Join):
                joins.append(node)
            nodes.extend(node.children)
        self.assertEqual([j.kind for j in joins], [JoinKind.THETA])
        self.assertLessEqual(optimized.estimated_cost, before)
        self.assertEqual(optimized.plan.columns, [ColumnRef("student", "name")])

    def test_update_tetap_scan_dengan_filter(self) -> None:
        optimized = self.qo.optimize_query(self.qo.parse_query("UPDATE student SET gpa = 4.0 WHERE id = 1 AND gpa < 2.0;"))
        self.assertIsInstance(optimized.plan, Update)
        source = optimized.plan.children[0]
        while isinstance(source, Filter):
            source = source.children[0]
        self.assertIsInstance(source, TableScan)

    def test_delete_tanpa_where(self) -> None:
        optimized = self.qo.optimize_query(self.qo.parse_query("DELETE FROM student;"))
        self.assertIsInstance(optimized.plan, Delete)
        self.assertIsInstance(optimized.plan.children[0], TableScan)

    def test_transaksi_tidak_berubah(self) -> None:
        parsed = self.qo.parse_query("BEGIN;")
        self.assertIs(self.qo.optimize_query(parsed), parsed)

    def test_rewrite_gagal_memakai_plan_asli(self) -> None:
        parsed = self.qo.parse_query("SELECT name FROM student WHERE id = 1;")
        asli = parsed.plan

        def rusak(query):
            raise RuntimeError("bug planner")

        self.qo._planner.optimize = rusak
        with self.assertLogs("query_optimizer.optimizer", level="WARNING"):
            optimized = self.qo.optimize_query(parsed)
        self.assertIs(optimized.plan, asli)


class TestCostExplain(FacadeCase):
    def test_biaya_tulis(self) -> None:
        update = self.qo.parse_query("UPDATE student SET gpa = 4.0 WHERE id = 1;").plan
        self.assertEqual(self.qo.get_cost(update), self.qo.get_cost(update.children[0]))
        self.assertEqual(self.qo.get_cost(self.qo.parse_query("INSERT INTO course VALUES (1, 'Basis Data', 3);").plan), 0)

    def test_explain_select_dan_update(self) -> None:
        for sql in ("EXPLAIN SELECT * FROM student WHERE id = 1;", "EXPLAIN UPDATE student SET gpa = 4.0 WHERE id = 1;"):
            with self.subTest(sql=sql):
                teks = self.qo.explain(self.qo.parse_query(sql))
                self.assertIn("SEBELUM (biaya", teks)
                self.assertIn("SESUDAH (biaya", teks)

    def test_explain_tidak_mengubah_plan(self) -> None:
        parsed = self.qo.parse_query("SELECT student.name FROM student, course WHERE student.id = course.id;")
        self.assertIsInstance(parsed.plan, Project)
        sebelum = repr(parsed.plan)
        self.qo.explain(parsed)
        self.assertEqual(repr(parsed.plan), sebelum)


if __name__ == "__main__":
    unittest.main()
