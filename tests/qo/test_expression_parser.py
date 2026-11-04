"""Uji parser ekspresi dan kondisi QO-04."""

from __future__ import annotations

import unittest

from common.catalog import DataType
from common.errors import QuerySyntaxError
from common.expression import (
    And, ArithOp, BinaryOp, ColumnRef, Comparison, CompOp, Literal,
)
from query_optimizer.expression_parser import ExpressionParser
from query_optimizer.lexer import Lexer, TokenType


def int_lit(v: int) -> Literal:
    return Literal(v, DataType.INTEGER)


class ExpressionParserTest(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = ExpressionParser()
        self.lexer = Lexer()

    def expr(self, sql: str):
        tokens = self.lexer.tokenize(sql)
        result = self.parser.parse_expression(tokens)
        self.assertTrue(tokens.is_at_end(), "token tersisa setelah parse")
        return result

    def cond(self, sql: str):
        tokens = self.lexer.tokenize(sql)
        result = self.parser.parse_condition(tokens)
        self.assertTrue(tokens.is_at_end(), "token tersisa setelah parse")
        return result

    def test_literal_dan_kolom(self) -> None:
        self.assertEqual(self.expr("42"), int_lit(42))
        self.assertEqual(self.expr("3.5"), Literal(3.5, DataType.FLOAT))
        self.assertEqual(self.expr("'abc'"), Literal("abc", DataType.VARCHAR))
        self.assertEqual(self.expr("gpa"), ColumnRef(None, "gpa"))
        self.assertEqual(self.expr("student.gpa"), ColumnRef("student", "gpa"))

    def test_prioritas_operator(self) -> None:
        self.assertEqual(
            self.expr("1 + 2 * 3"),
            BinaryOp(ArithOp.ADD, int_lit(1), BinaryOp(ArithOp.MUL, int_lit(2), int_lit(3))),
        )

    def test_asosiatif_kiri(self) -> None:
        self.assertEqual(
            self.expr("10 - 3 - 2"),
            BinaryOp(ArithOp.SUB, BinaryOp(ArithOp.SUB, int_lit(10), int_lit(3)), int_lit(2)),
        )
        self.assertEqual(
            self.expr("8 / 4 / 2"),
            BinaryOp(ArithOp.DIV, BinaryOp(ArithOp.DIV, int_lit(8), int_lit(4)), int_lit(2)),
        )

    def test_kurung(self) -> None:
        self.assertEqual(
            self.expr("(1 + 2) * 3"),
            BinaryOp(ArithOp.MUL, BinaryOp(ArithOp.ADD, int_lit(1), int_lit(2)), int_lit(3)),
        )

    def test_minus_unary(self) -> None:
        self.assertEqual(self.expr("-5"), int_lit(-5))
        self.assertEqual(self.expr("-2.5"), Literal(-2.5, DataType.FLOAT))
        self.assertEqual(
            self.expr("-gpa"),
            BinaryOp(ArithOp.SUB, int_lit(0), ColumnRef(None, "gpa")),
        )
        self.assertEqual(self.expr("--3"), int_lit(3))
        self.assertEqual(
            self.expr("a - -b"),
            BinaryOp(
                ArithOp.SUB,
                ColumnRef(None, "a"),
                BinaryOp(ArithOp.SUB, int_lit(0), ColumnRef(None, "b")),
            ),
        )

    def test_semua_operator_perbandingan(self) -> None:
        kasus = {
            "=": CompOp.EQ, "<>": CompOp.NE, "!=": CompOp.NE, ">": CompOp.GT,
            ">=": CompOp.GE, "<": CompOp.LT, "<=": CompOp.LE,
        }
        for simbol, op in kasus.items():
            with self.subTest(simbol=simbol):
                self.assertEqual(
                    self.cond(f"a {simbol} 1"),
                    Comparison(op, ColumnRef(None, "a"), int_lit(1)),
                )

    def test_perbandingan_dengan_aritmetika(self) -> None:
        self.assertEqual(
            self.cond("a + 1 > b * 2"),
            Comparison(
                CompOp.GT,
                BinaryOp(ArithOp.ADD, ColumnRef(None, "a"), int_lit(1)),
                BinaryOp(ArithOp.MUL, ColumnRef(None, "b"), int_lit(2)),
            ),
        )

    def test_and_berantai(self) -> None:
        hasil = self.cond("a > 1 AND b < 2 and c = 'x'")
        self.assertEqual(
            hasil,
            And([
                Comparison(CompOp.GT, ColumnRef(None, "a"), int_lit(1)),
                Comparison(CompOp.LT, ColumnRef(None, "b"), int_lit(2)),
                Comparison(CompOp.EQ, ColumnRef(None, "c"), Literal("x", DataType.VARCHAR)),
            ]),
        )

    def test_satu_kondisi_tidak_dibungkus_and(self) -> None:
        self.assertIsInstance(self.cond("a = 1"), Comparison)

    def test_berhenti_sebelum_keyword_berikutnya(self) -> None:
        tokens = self.lexer.tokenize("a = 1 ORDER BY a")
        self.parser.parse_condition(tokens)
        self.assertTrue(tokens.match(TokenType.KEYWORD, "ORDER"))

    def test_error_sintaks(self) -> None:
        salah = [
            "a >",          # operand kanan hilang
            "a 1",          # operator perbandingan hilang
            "(1 + 2",       # kurung tidak ditutup
            "a = 1 AND",    # operand setelah AND hilang
            "a + * 2 > 1",  # operator berurutan
            "student. > 1", # nama kolom setelah titik hilang
            "",             # kosong
        ]
        for sql in salah:
            with self.subTest(sql=sql):
                with self.assertRaises(QuerySyntaxError):
                    self.parser.parse_condition(self.lexer.tokenize(sql))

    def test_ekspresi_tanpa_perbandingan_ditolak_sebagai_kondisi(self) -> None:
        with self.assertRaises(QuerySyntaxError):
            self.parser.parse_condition(self.lexer.tokenize("a + 1"))


if __name__ == "__main__":
    unittest.main()