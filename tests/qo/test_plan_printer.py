"""Uji tampilan query plan untuk EXPLAIN (QO-15)."""

import unittest

from common.catalog import ColumnSchema, DataType, IndexInfo, IndexKind, TableSchema
from common.expression import And, ArithOp, BinaryOp, ColumnRef, Comparison, CompOp, Literal, Subquery
from common.plan import (
    CreateIndex,
    CreateTable,
    Delete,
    DropTable,
    Filter,
    IndexScan,
    Insert,
    Join,
    JoinKind,
    Limit,
    PlanNode,
    Project,
    Sort,
    SubqueryScan,
    TableScan,
    Update,
)
from query_optimizer.plan_printer import PlanPrinter, render_plan

ID = ColumnRef("student", "id")
GPA = ColumnRef("student", "gpa")
NAME = ColumnRef("student", "name")
IDX_ID = IndexInfo("idx_id", "student", "id")


def lit(value: object) -> Literal:
    match value:
        case None:
            return Literal(None, DataType.INTEGER)
        case int():
            return Literal(value, DataType.INTEGER)
        case float():
            return Literal(value, DataType.FLOAT)
        case _:
            return Literal(value, DataType.VARCHAR)


class TestRenderExpr(unittest.TestCase):
    def setUp(self) -> None:
        self.p = PlanPrinter()

    def test_column_ref(self) -> None:
        self.assertEqual(self.p._render_expr(ID), "student.id")
        self.assertEqual(self.p._render_expr(ColumnRef(None, "id")), "id")

    def test_literal(self) -> None:
        self.assertEqual(self.p._render_expr(lit(7)), "7")
        self.assertEqual(self.p._render_expr(lit(3.5)), "3.5")
        self.assertEqual(self.p._render_expr(lit("Ani")), "'Ani'")
        self.assertEqual(self.p._render_expr(lit(None)), "NULL")

    def test_literal_angka_dalam_string_tetap_berkutip(self) -> None:
        self.assertEqual(self.p._render_expr(lit("10")), "'10'")

    def test_binary_op_berkurung(self) -> None:
        expr = BinaryOp(ArithOp.MUL, BinaryOp(ArithOp.ADD, GPA, lit(1)), lit(2))
        self.assertEqual(self.p._render_expr(expr), "((student.gpa + 1) * 2)")

    def test_comparison_semua_operator(self) -> None:
        for op, simbol in [(CompOp.EQ, "="), (CompOp.NE, "<>"), (CompOp.GT, ">"),
                           (CompOp.GE, ">="), (CompOp.LT, "<"), (CompOp.LE, "<=")]:
            with self.subTest(op=op):
                self.assertEqual(self.p._render_expr(Comparison(op, ID, lit(1))), f"student.id {simbol} 1")

    def test_and(self) -> None:
        expr = And([Comparison(CompOp.GT, GPA, lit(3.5)), Comparison(CompOp.EQ, NAME, lit("Ani"))])
        self.assertEqual(self.p._render_expr(expr), "student.gpa > 3.5 AND student.name = 'Ani'")

    def test_subquery(self) -> None:
        expr = Comparison(CompOp.GT, GPA, Subquery(TableScan(table="student")))
        self.assertEqual(self.p._render_expr(expr), "student.gpa > (subquery)")


class TestLabel(unittest.TestCase):
    def setUp(self) -> None:
        self.p = PlanPrinter()

    def check(self, node: PlanNode, expected: str) -> None:
        self.assertEqual(self.p._label(node), expected)

    def test_table_scan(self) -> None:
        self.check(TableScan(table="student"), "TableScan [student]")
        self.check(TableScan(table="student", alias="s"), "TableScan [student AS s]")

    def test_index_scan_kesamaan(self) -> None:
        self.check(IndexScan(table="student", index=IDX_ID, op=CompOp.EQ, key=7),
                   "IndexScan [student.idx_id: id = 7]")

    def test_index_scan_rentang(self) -> None:
        self.check(IndexScan(table="student", index=IDX_ID, op=CompOp.GE, low=1, high=10),
                   "IndexScan [student.idx_id: 1 <= id <= 10]")

    def test_index_scan_rentang_satu_sisi(self) -> None:
        self.check(IndexScan(table="student", index=IDX_ID, op=CompOp.GE, low=5),
                   "IndexScan [student.idx_id: 5 <= id]")
        self.check(IndexScan(table="student", index=IDX_ID, op=CompOp.LE, high=5),
                   "IndexScan [student.idx_id: id <= 5]")

    def test_filter(self) -> None:
        self.check(Filter(condition=Comparison(CompOp.GT, GPA, lit(3.5))), "Filter [student.gpa > 3.5]")

    def test_project_bintang(self) -> None:
        self.check(Project(columns=[]), "Project [*]")

    def test_project_tanpa_alias(self) -> None:
        self.check(Project(columns=[NAME, ID]), "Project [student.name, student.id]")

    def test_project_alias_sebagian(self) -> None:
        self.check(Project(columns=[NAME, ID], aliases=["nama", None]),
                   "Project [student.name AS nama, student.id]")

    def test_join_theta(self) -> None:
        cond = Comparison(CompOp.EQ, ID, ColumnRef("enrollment", "student_id"))
        self.check(Join(kind=JoinKind.THETA, condition=cond),
                   "Join BNLJ THETA [student.id = enrollment.student_id]")

    def test_join_tanpa_kondisi(self) -> None:
        self.check(Join(kind=JoinKind.CROSS), "Join BNLJ CROSS")
        self.check(Join(kind=JoinKind.NATURAL), "Join BNLJ NATURAL")

    def test_sort(self) -> None:
        self.check(Sort(column=GPA, ascending=False), "Sort [student.gpa DESC]")
        self.check(Sort(column=GPA), "Sort [student.gpa ASC]")

    def test_limit_dan_subquery_scan(self) -> None:
        self.check(Limit(count=10), "Limit [10]")
        self.check(SubqueryScan(alias="t"), "SubqueryScan [t]")

    def test_perubahan_data(self) -> None:
        self.check(Insert(table="student", values=[lit(6), lit("Budi"), lit(None)]),
                   "Insert [student: (6, 'Budi', NULL)]")
        self.check(Update(table="student", column="gpa", expression=BinaryOp(ArithOp.ADD, GPA, lit(0.1))),
                   "Update [student: gpa = (student.gpa + 0.1)]")
        self.check(Delete(table="student"), "Delete [student]")

    def test_ddl(self) -> None:
        self.check(CreateIndex(index=IndexInfo("idx_gpa", "student", "gpa", IndexKind.HASH)),
                   "CreateIndex [idx_gpa ON student(gpa) USING HASH]")
        schema = TableSchema("course", [ColumnSchema("id", DataType.INTEGER)])
        self.check(CreateTable(schema=schema), "CreateTable [course]")
        self.check(DropTable(table="course"), "DropTable [course RESTRICT]")
        self.check(DropTable(table="course", cascade=True), "DropTable [course CASCADE]")

    def test_fallback_tidak_crash(self) -> None:
        self.check(PlanNode(), "PlanNode")


class TestRender(unittest.TestCase):
    def setUp(self) -> None:
        self.p = PlanPrinter()
        join = Join(
            kind=JoinKind.THETA,
            condition=Comparison(CompOp.EQ, ID, ColumnRef("enrollment", "student_id")),
            children=[TableScan(table="student"), TableScan(table="enrollment")],
        )
        self.plan = Limit(count=5, children=[
            Sort(column=GPA, ascending=False, children=[Project(columns=[NAME], children=[join])]),
        ])

    def test_plan_none(self) -> None:
        self.assertEqual(self.p.render(None), "(tidak ada plan)")

    def test_satu_node(self) -> None:
        self.assertEqual(self.p.render(TableScan(table="student")), "TableScan [student]")

    def test_pohon_berindentasi(self) -> None:
        self.assertEqual(self.p.render(self.plan), "\n".join([
            "Limit [5]",
            "  Sort [student.gpa DESC]",
            "    Project [student.name]",
            "      Join BNLJ THETA [student.id = enrollment.student_id]",
            "        TableScan [student]",
            "        TableScan [enrollment]",
        ]))

    def test_urutan_anak_kiri_dulu(self) -> None:
        join = Join(kind=JoinKind.CROSS, children=[TableScan(table="b"), TableScan(table="a")])
        baris = self.p.render(join).splitlines()
        self.assertEqual(baris[1:], ["  TableScan [b]", "  TableScan [a]"])

    def test_subquery_scan_mencetak_plan_di_bawahnya(self) -> None:
        plan = SubqueryScan(alias="t", children=[TableScan(table="student")])
        self.assertEqual(self.p.render(plan), "SubqueryScan [t]\n  TableScan [student]")

    def test_render_tidak_mengubah_plan(self) -> None:
        sebelum = repr(self.plan)
        self.p.render(self.plan)
        self.assertEqual(repr(self.plan), sebelum)

    def test_render_plan_sama_dengan_printer(self) -> None:
        self.assertEqual(render_plan(self.plan), self.p.render(self.plan))
        self.assertEqual(render_plan(None), "(tidak ada plan)")


class TestBuildExplain(unittest.TestCase):
    def setUp(self) -> None:
        self.p = PlanPrinter()

    def test_format_lengkap(self) -> None:
        before = Filter(condition=Comparison(CompOp.EQ, ID, lit(7)), children=[TableScan(table="student")])
        after = IndexScan(table="student", index=IDX_ID, op=CompOp.EQ, key=7)
        self.assertEqual(self.p.build_explain(before, after, 130.0, 22.0), "\n".join([
            "SEBELUM (biaya 130 blok)",
            "Filter [student.id = 7]",
            "  TableScan [student]",
            "",
            "SESUDAH (biaya 22 blok)",
            "IndexScan [student.idx_id: id = 7]",
        ]))

    def test_biaya_pecahan(self) -> None:
        hasil = self.p.build_explain(TableScan(table="a"), TableScan(table="a"), 12.5, 0.25)
        self.assertIn("SEBELUM (biaya 12.5 blok)", hasil)
        self.assertIn("SESUDAH (biaya 0.25 blok)", hasil)

    def test_plan_none(self) -> None:
        hasil = self.p.build_explain(None, None, 0.0, 0.0)
        self.assertEqual(hasil.count("(tidak ada plan)"), 2)
        self.assertIn("SEBELUM (biaya 0 blok)", hasil)


if __name__ == "__main__":
    unittest.main()
