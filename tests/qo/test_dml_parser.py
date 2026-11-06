import unittest

from common.catalog import DataType
from common.errors import QuerySyntaxError
from common.expression import And, ArithOp, BinaryOp, ColumnRef, CompOp, Comparison, Literal
from common.plan import Delete, Filter, Insert, QueryKind, TableScan, Update
from query_optimizer.lexer import Lexer, TokenType
from query_optimizer.statement_parser import StatementParser

T = TokenType


def _int(v: int) -> Literal:
    return Literal(v, DataType.INTEGER)


def _gpa_lebih_dari_3() -> Comparison:
    return Comparison(CompOp.GT, ColumnRef(None, "gpa"), Literal(3, DataType.INTEGER))


def _delete(sql: str):
    stream = Lexer().tokenize(sql)
    return StatementParser().parse_delete(stream), stream


def _insert(sql: str):
    stream = Lexer().tokenize(sql)
    return StatementParser().parse_insert(stream), stream


def _update(sql: str):
    stream = Lexer().tokenize(sql)
    return StatementParser().parse_update(stream), stream


def _gagal(test: unittest.TestCase, parse, sql: str, potongan: str = "") -> None:
    with test.assertRaises(QuerySyntaxError) as ctx:
        parse(sql)
    test.assertIn("Kesalahan sintaks", str(ctx.exception))
    test.assertIn(potongan, str(ctx.exception))


class TestDelete(unittest.TestCase):
    def test_tanpa_where(self) -> None:
        hasil, stream = _delete("DELETE FROM student;")
        self.assertEqual(hasil.kind, QueryKind.DELETE)
        self.assertEqual(hasil.sql, "")
        self.assertEqual(hasil.plan, Delete(table="student", children=[TableScan(table="student")]))
        self.assertTrue(stream.match(T.SEMICOLON))

    def test_dengan_where(self) -> None:
        hasil, _ = _delete("DELETE FROM student WHERE gpa > 3")
        self.assertEqual(hasil.plan, Delete(
            table="student",
            children=[Filter(condition=_gpa_lebih_dari_3(), children=[TableScan(table="student")])],
        ))

    def test_where_dengan_and(self) -> None:
        hasil, _ = _delete("DELETE FROM student WHERE gpa > 3 AND id < 10")
        kondisi = hasil.plan.children[0].condition
        self.assertIsInstance(kondisi, And)
        self.assertEqual(len(kondisi.conditions), 2)

    def test_keyword_tidak_case_sensitive(self) -> None:
        hasil, _ = _delete("delete from student where gpa > 3")
        self.assertIsInstance(hasil.plan, Delete)
        self.assertIsInstance(hasil.plan.children[0], Filter)

    def test_multi_baris(self) -> None:
        hasil, _ = _delete("DELETE\nFROM student\nWHERE gpa > 3\n;")
        self.assertEqual(hasil.plan.table, "student")

    def test_gagal_tanpa_from(self) -> None:
        _gagal(self, _delete, "DELETE student", "FROM")

    def test_gagal_tanpa_tabel(self) -> None:
        _gagal(self, _delete, "DELETE FROM", "IDENT")

    def test_gagal_where_tanpa_kondisi(self) -> None:
        _gagal(self, _delete, "DELETE FROM student WHERE")

    def test_gagal_where_tanpa_pembanding(self) -> None:
        _gagal(self, _delete, "DELETE FROM student WHERE gpa", "operator perbandingan")


class TestInsert(unittest.TestCase):
    def test_semua_tipe(self) -> None:
        hasil, stream = _insert("INSERT INTO student VALUES (1, 'Ani', 3.75, 'F');")
        self.assertEqual(hasil.kind, QueryKind.INSERT)
        self.assertEqual(hasil.sql, "")
        self.assertEqual(hasil.plan, Insert(table="student", values=[
            _int(1), Literal("Ani", DataType.VARCHAR), Literal(3.75, DataType.FLOAT),
            Literal("F", DataType.VARCHAR),
        ]))
        self.assertTrue(stream.match(T.SEMICOLON))

    def test_satu_nilai(self) -> None:
        hasil, _ = _insert("INSERT INTO t VALUES (7)")
        self.assertEqual(hasil.plan.values, [_int(7)])

    def test_angka_negatif(self) -> None:
        hasil, _ = _insert("INSERT INTO t VALUES (-5, -2.5)")
        self.assertEqual(hasil.plan.values, [_int(-5), Literal(-2.5, DataType.FLOAT)])

    def test_keyword_tidak_case_sensitive(self) -> None:
        hasil, _ = _insert("insert into student values (1)")
        self.assertIsInstance(hasil.plan, Insert)

    def test_gagal_tanpa_values(self) -> None:
        _gagal(self, _insert, "INSERT INTO student (1, 2)", "VALUES")

    def test_gagal_tanpa_into(self) -> None:
        _gagal(self, _insert, "INSERT student VALUES (1)", "INTO")

    def test_gagal_kurung_tidak_ditutup(self) -> None:
        _gagal(self, _insert, "INSERT INTO student VALUES (1, 2", ")")

    def test_gagal_tanpa_kurung_buka(self) -> None:
        _gagal(self, _insert, "INSERT INTO student VALUES 1, 2", "(")

    def test_gagal_nilai_kosong(self) -> None:
        _gagal(self, _insert, "INSERT INTO student VALUES ()", "kosong")

    def test_gagal_koma_menggantung(self) -> None:
        _gagal(self, _insert, "INSERT INTO student VALUES (1,)")

    def test_gagal_nilai_bukan_konstanta(self) -> None:
        _gagal(self, _insert, "INSERT INTO student VALUES (nama)", "konstanta")

    def test_gagal_nilai_ekspresi(self) -> None:
        _gagal(self, _insert, "INSERT INTO student VALUES (1 + 2)", "konstanta")


class TestUpdate(unittest.TestCase):
    def test_nilai_konstan_tanpa_where(self) -> None:
        hasil, stream = _update("UPDATE student SET gpa = 4;")
        self.assertEqual(hasil.kind, QueryKind.UPDATE)
        self.assertEqual(hasil.sql, "")
        self.assertEqual(hasil.plan, Update(
            table="student", column="gpa", expression=_int(4),
            children=[TableScan(table="student")],
        ))
        self.assertTrue(stream.match(T.SEMICOLON))

    def test_ekspresi_aritmetika(self) -> None:
        hasil, _ = _update("UPDATE student SET gpa = gpa + 1")
        self.assertEqual(hasil.plan.expression,
                         BinaryOp(ArithOp.ADD, ColumnRef(None, "gpa"), _int(1)))

    def test_prioritas_operator(self) -> None:
        hasil, _ = _update("UPDATE t SET a = b + c * 2")
        self.assertEqual(hasil.plan.expression, BinaryOp(
            ArithOp.ADD, ColumnRef(None, "b"),
            BinaryOp(ArithOp.MUL, ColumnRef(None, "c"), _int(2)),
        ))

    def test_dengan_where(self) -> None:
        hasil, _ = _update("UPDATE student SET gpa = 4 WHERE gpa > 3")
        self.assertEqual(hasil.plan.children, [
            Filter(condition=_gpa_lebih_dari_3(), children=[TableScan(table="student")]),
        ])

    def test_set_string(self) -> None:
        hasil, _ = _update("UPDATE student SET name = 'Budi' WHERE id = 1")
        self.assertEqual(hasil.plan.column, "name")
        self.assertEqual(hasil.plan.expression, Literal("Budi", DataType.VARCHAR))

    def test_keyword_tidak_case_sensitive(self) -> None:
        hasil, _ = _update("update student set gpa = 4 where id = 1")
        self.assertIsInstance(hasil.plan, Update)

    def test_gagal_tanpa_set(self) -> None:
        _gagal(self, _update, "UPDATE student gpa = 4", "SET")

    def test_gagal_tanpa_sama_dengan(self) -> None:
        _gagal(self, _update, "UPDATE student SET gpa 4", "=")

    def test_gagal_tanpa_ekspresi(self) -> None:
        _gagal(self, _update, "UPDATE student SET gpa =")

    def test_gagal_dua_kolom(self) -> None:
        _gagal(self, _update, "UPDATE student SET gpa = 4, name = 'x'", "satu kolom")

    def test_gagal_tanpa_tabel(self) -> None:
        _gagal(self, _update, "UPDATE SET gpa = 4", "IDENT")

    def test_gagal_where_tanpa_kondisi(self) -> None:
        _gagal(self, _update, "UPDATE student SET gpa = 4 WHERE")


if __name__ == "__main__":
    unittest.main()