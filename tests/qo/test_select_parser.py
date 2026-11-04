import unittest

from common.catalog import DataType
from common.errors import QuerySyntaxError
from common.expression import ColumnRef, CompOp, Comparison, Expression, Literal
from common.plan import Filter, Join, JoinKind, Project, QueryKind, Sort, TableScan
from query_optimizer.lexer import Lexer, TokenStream, TokenType
from query_optimizer.select_parser import SelectParser

T = TokenType


class _FakeExprParser:
    def parse_condition(self, tokens: TokenStream) -> Expression:
        nama = tokens.expect(T.IDENT).value
        tabel = None
        if tokens.match(T.DOT):
            tokens.advance()
            tabel, nama = nama, tokens.expect(T.IDENT).value
        tokens.expect(T.OP, "=")
        nilai = tokens.expect(T.INTEGER).value
        return Comparison(op=CompOp.EQ, left=ColumnRef(tabel, nama), right=Literal(nilai, DataType.INTEGER))


def _parse(sql: str):
    stream = Lexer().tokenize(sql)
    hasil = SelectParser(_FakeExprParser()).parse_select(stream)
    return hasil, stream


def _gagal(test: unittest.TestCase, sql: str, potongan: str) -> None:
    with test.assertRaises(QuerySyntaxError) as ctx:
        _parse(sql)
    test.assertIn(potongan, str(ctx.exception))


class TestSelectList(unittest.TestCase):
    def test_select_bintang(self) -> None:
        hasil, stream = _parse("SELECT * FROM student;")
        self.assertEqual(hasil.kind, QueryKind.SELECT)
        self.assertEqual(hasil.sql, "")
        self.assertIsInstance(hasil.plan, Project)
        self.assertEqual(hasil.plan.columns, [])
        self.assertEqual(hasil.plan.children, [TableScan(table="student")])
        self.assertTrue(stream.match(T.SEMICOLON))

    def test_daftar_kolom_urut(self) -> None:
        hasil, _ = _parse("SELECT name, id, gpa FROM student")
        self.assertEqual(hasil.plan.columns, [
            ColumnRef(None, "name"),
            ColumnRef(None, "id"),
            ColumnRef(None, "gpa"),
        ])

    def test_kolom_dengan_prefix_tabel(self) -> None:
        hasil, _ = _parse("SELECT student.name, id FROM student")
        self.assertEqual(hasil.plan.columns, [
            ColumnRef("student", "name"),
            ColumnRef(None, "id"),
        ])

    def test_daftar_kolom_kosong(self) -> None:
        _gagal(self, "SELECT FROM student", "daftar kolom kosong")

    def test_koma_tanpa_kolom(self) -> None:
        _gagal(self, "SELECT a, FROM student", "diharapkan IDENT")

    def test_dua_kolom_tanpa_koma(self) -> None:
        _gagal(self, "SELECT a b FROM student", "diharapkan FROM")

    def test_literal_di_select_ditolak(self) -> None:
        _gagal(self, "SELECT 1", "diharapkan IDENT")

    def test_tanpa_from(self) -> None:
        _gagal(self, "SELECT name", "diharapkan FROM")

    def test_prefix_tanpa_kolom(self) -> None:
        _gagal(self, "SELECT student. FROM student", "diharapkan IDENT")


class TestWhereOrderBy(unittest.TestCase):
    def test_where_jadi_filter(self) -> None:
        hasil, _ = _parse("SELECT name FROM student WHERE id = 1")
        filt = hasil.plan.children[0]
        self.assertIsInstance(filt, Filter)
        self.assertEqual(filt.condition.left, ColumnRef(None, "id"))
        self.assertEqual(filt.children, [TableScan(table="student")])

    def test_order_by_default_asc(self) -> None:
        hasil, _ = _parse("SELECT name FROM student ORDER BY name")
        sort = hasil.plan.children[0]
        self.assertIsInstance(sort, Sort)
        self.assertEqual(sort.column, ColumnRef(None, "name"))
        self.assertTrue(sort.ascending)

    def test_order_by_asc_eksplisit(self) -> None:
        hasil, _ = _parse("SELECT name FROM student ORDER BY name ASC")
        self.assertTrue(hasil.plan.children[0].ascending)

    def test_order_by_desc(self) -> None:
        hasil, _ = _parse("SELECT name FROM student ORDER BY student.gpa DESC")
        sort = hasil.plan.children[0]
        self.assertEqual(sort.column, ColumnRef("student", "gpa"))
        self.assertFalse(sort.ascending)

    def test_urutan_pohon_lengkap(self) -> None:
        hasil, stream = _parse("SELECT name, id FROM student WHERE id = 1 ORDER BY name;")
        project = hasil.plan
        sort = project.children[0]
        filt = sort.children[0]
        scan = filt.children[0]
        self.assertIsInstance(project, Project)
        self.assertIsInstance(sort, Sort)
        self.assertIsInstance(filt, Filter)
        self.assertEqual(scan, TableScan(table="student"))
        self.assertTrue(stream.match(T.SEMICOLON))

    def test_order_tanpa_by(self) -> None:
        _gagal(self, "SELECT name FROM student ORDER name", "diharapkan BY")

    def test_order_by_tanpa_kolom(self) -> None:
        _gagal(self, "SELECT name FROM student ORDER BY", "diharapkan IDENT")

    def test_order_by_kolom_tidak_di_select(self) -> None:
        hasil, _ = _parse("SELECT name FROM student ORDER BY id")
        self.assertEqual(hasil.plan.columns, [ColumnRef(None, "name")])
        self.assertEqual(hasil.plan.children[0].column, ColumnRef(None, "id"))


class TestFrom(unittest.TestCase):
    def test_koma_jadi_cross_join(self) -> None:
        hasil, _ = _parse("SELECT * FROM student, enroll")
        join = hasil.plan.children[0]
        self.assertEqual(join, Join(kind=JoinKind.CROSS, children=[
            TableScan(table="student"),
            TableScan(table="enroll"),
        ]))

    def test_tiga_tabel_kiri_ke_kanan(self) -> None:
        hasil, _ = _parse("SELECT * FROM a, b, c")
        luar = hasil.plan.children[0]
        self.assertEqual(luar.kind, JoinKind.CROSS)
        self.assertEqual(luar.children[1], TableScan(table="c"))
        dalam = luar.children[0]
        self.assertEqual(dalam.children, [TableScan(table="a"), TableScan(table="b")])

    def test_join_on(self) -> None:
        hasil, _ = _parse("SELECT * FROM student JOIN enroll ON student.id = 1")
        join = hasil.plan.children[0]
        self.assertEqual(join.kind, JoinKind.THETA)
        self.assertEqual(join.condition.left, ColumnRef("student", "id"))
        self.assertEqual(join.children, [TableScan(table="student"), TableScan(table="enroll")])

    def test_natural_join(self) -> None:
        hasil, _ = _parse("SELECT * FROM student NATURAL JOIN enroll")
        join = hasil.plan.children[0]
        self.assertEqual(join.kind, JoinKind.NATURAL)
        self.assertIsNone(join.condition)

    def test_campuran_kiri_ke_kanan(self) -> None:
        hasil, _ = _parse("SELECT * FROM a NATURAL JOIN b JOIN c ON c.x = 1, d")
        cross = hasil.plan.children[0]
        self.assertEqual(cross.kind, JoinKind.CROSS)
        self.assertEqual(cross.children[1], TableScan(table="d"))
        theta = cross.children[0]
        self.assertEqual(theta.kind, JoinKind.THETA)
        self.assertEqual(theta.children[1], TableScan(table="c"))
        natural = theta.children[0]
        self.assertEqual(natural.kind, JoinKind.NATURAL)
        self.assertEqual(natural.children, [TableScan(table="a"), TableScan(table="b")])

    def test_join_lalu_where(self) -> None:
        hasil, _ = _parse("SELECT * FROM a JOIN b ON a.x = 1 WHERE y = 2")
        filt = hasil.plan.children[0]
        self.assertIsInstance(filt, Filter)
        self.assertEqual(filt.children[0].kind, JoinKind.THETA)

    def test_join_tanpa_on(self) -> None:
        _gagal(self, "SELECT * FROM a JOIN b;", "diharapkan ON")

    def test_natural_tanpa_join(self) -> None:
        _gagal(self, "SELECT * FROM a NATURAL b", "diharapkan JOIN")

    def test_from_kosong(self) -> None:
        _gagal(self, "SELECT * FROM", "diharapkan IDENT")

    def test_koma_tanpa_tabel(self) -> None:
        _gagal(self, "SELECT * FROM a,", "diharapkan IDENT")


if __name__ == "__main__":
    unittest.main()
