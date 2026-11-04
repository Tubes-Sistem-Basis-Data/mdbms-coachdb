import unittest

from common.errors import QuerySyntaxError
from query_optimizer.lexer import Lexer, Token, TokenStream, TokenType

T = TokenType


def _lex(sql: str) -> list[Token]:
    stream = Lexer().tokenize(sql)
    hasil = []
    while True:
        tok = stream.advance()
        hasil.append(tok)
        if tok.type == T.EOF:
            return hasil


def _pairs(sql: str) -> list[tuple[TokenType, object]]:
    return [(t.type, t.value) for t in _lex(sql)]


class TestLexerBiasa(unittest.TestCase):
    def test_select_sederhana(self) -> None:
        self.assertEqual(_pairs("SELECT * FROM student;"), [
            (T.KEYWORD, "SELECT"),
            (T.OP, "*"),
            (T.KEYWORD, "FROM"),
            (T.IDENT, "student"),
            (T.SEMICOLON, ";"),
            (T.EOF, ""),
        ])

    def test_input_kosong(self) -> None:
        self.assertEqual(_pairs(""), [(T.EOF, "")])

    def test_hanya_whitespace(self) -> None:
        self.assertEqual(_pairs("  \t\n  "), [(T.EOF, "")])

    def test_query_lengkap(self) -> None:
        self.assertEqual(_pairs("UPDATE t SET x = x * 2 WHERE id >= 10;"), [
            (T.KEYWORD, "UPDATE"),
            (T.IDENT, "t"),
            (T.KEYWORD, "SET"),
            (T.IDENT, "x"),
            (T.OP, "="),
            (T.IDENT, "x"),
            (T.OP, "*"),
            (T.INTEGER, 2),
            (T.KEYWORD, "WHERE"),
            (T.IDENT, "id"),
            (T.OP, ">="),
            (T.INTEGER, 10),
            (T.SEMICOLON, ";"),
            (T.EOF, ""),
        ])


class TestLexerKata(unittest.TestCase):
    def test_keyword_huruf_kecil_jadi_kapital(self) -> None:
        self.assertEqual(_pairs("select")[0], (T.KEYWORD, "SELECT"))

    def test_keyword_campuran(self) -> None:
        self.assertEqual(_pairs("SeLeCt")[0], (T.KEYWORD, "SELECT"))

    def test_identifier_tetap_case_aslinya(self) -> None:
        self.assertEqual(_pairs("Student")[0], (T.IDENT, "Student"))

    def test_identifier_dengan_underscore_dan_angka(self) -> None:
        self.assertEqual(_pairs("_nilai_akhir2")[0], (T.IDENT, "_nilai_akhir2"))

    def test_awalan_keyword_tetap_identifier(self) -> None:
        self.assertEqual(_pairs("selected")[0], (T.IDENT, "selected"))

    def test_kolom_bertabel(self) -> None:
        self.assertEqual(_pairs("student.id")[:3], [
            (T.IDENT, "student"),
            (T.DOT, "."),
            (T.IDENT, "id"),
        ])


class TestLexerAngka(unittest.TestCase):
    def test_integer(self) -> None:
        self.assertEqual(_pairs("42")[0], (T.INTEGER, 42))

    def test_float(self) -> None:
        self.assertEqual(_pairs("3.5")[0], (T.FLOAT, 3.5))

    def test_value_bertipe_python_yang_benar(self) -> None:
        toks = _lex("7 7.0")
        self.assertIsInstance(toks[0].value, int)
        self.assertIsInstance(toks[1].value, float)

    def test_titik_tanpa_digit_setelahnya(self) -> None:
        self.assertEqual(_pairs("3.")[:2], [(T.INTEGER, 3), (T.DOT, ".")])

    def test_minus_bukan_bagian_angka(self) -> None:
        self.assertEqual(_pairs("-5")[:2], [(T.OP, "-"), (T.INTEGER, 5)])

    def test_angka_diikuti_huruf_ditolak(self) -> None:
        with self.assertRaises(QuerySyntaxError) as ctx:
            Lexer().tokenize("3abc")
        self.assertIn("angka tidak valid", str(ctx.exception))

    def test_float_diikuti_huruf_ditolak(self) -> None:
        with self.assertRaises(QuerySyntaxError) as ctx:
            Lexer().tokenize("3.5x")
        self.assertIn("angka tidak valid", str(ctx.exception))


class TestLexerString(unittest.TestCase):
    def test_kutip_tunggal(self) -> None:
        self.assertEqual(_pairs("'Budi Santoso'")[0], (T.STRING, "Budi Santoso"))

    def test_kutip_ganda(self) -> None:
        self.assertEqual(_pairs('"Budi"')[0], (T.STRING, "Budi"))

    def test_string_kosong(self) -> None:
        self.assertEqual(_pairs("''")[0], (T.STRING, ""))

    def test_kutip_lain_di_dalam_string(self) -> None:
        self.assertEqual(_pairs('"it\'s"')[0], (T.STRING, "it's"))

    def test_isi_string_tidak_jadi_keyword(self) -> None:
        self.assertEqual(_pairs("'select'")[0], (T.STRING, "select"))

    def test_string_tidak_ditutup(self) -> None:
        with self.assertRaises(QuerySyntaxError) as ctx:
            Lexer().tokenize("SELECT 'abc")
        self.assertIn("string tidak ditutup", str(ctx.exception))
        self.assertEqual(ctx.exception.position, 7)


class TestLexerOperator(unittest.TestCase):
    def test_operator_satu_karakter(self) -> None:
        self.assertEqual(_pairs("= < > + - * /")[:-1], [
            (T.OP, "="),
            (T.OP, "<"),
            (T.OP, ">"),
            (T.OP, "+"),
            (T.OP, "-"),
            (T.OP, "*"),
            (T.OP, "/"),
        ])

    def test_operator_dua_karakter(self) -> None:
        self.assertEqual(_pairs(">= <= <>")[:-1], [
            (T.OP, ">="),
            (T.OP, "<="),
            (T.OP, "<>"),
        ])

    def test_operator_tanpa_spasi(self) -> None:
        self.assertEqual(_pairs("a>=b")[:-1], [
            (T.IDENT, "a"),
            (T.OP, ">="),
            (T.IDENT, "b"),
        ])

    def test_tidak_sama_dengan_dinormalisasi(self) -> None:
        self.assertEqual(_pairs("!=")[0], (T.OP, "<>"))

    def test_seru_sendirian_ditolak(self) -> None:
        with self.assertRaises(QuerySyntaxError):
            Lexer().tokenize("a ! b")

    def test_tanda_baca(self) -> None:
        self.assertEqual(_pairs(", . ( ) ;")[:-1], [
            (T.COMMA, ","),
            (T.DOT, "."),
            (T.LPAREN, "("),
            (T.RPAREN, ")"),
            (T.SEMICOLON, ";"),
        ])

    def test_karakter_tidak_dikenal(self) -> None:
        with self.assertRaises(QuerySyntaxError) as ctx:
            Lexer().tokenize("SELECT @")
        self.assertIn("karakter tidak dikenal", str(ctx.exception))
        self.assertEqual(ctx.exception.token, "@")
        self.assertEqual(ctx.exception.position, 7)


class TestLexerPosisi(unittest.TestCase):
    def test_posisi_satu_baris(self) -> None:
        toks = _lex("SELECT * FROM t")
        self.assertEqual([(t.line, t.col, t.pos) for t in toks[:4]], [
            (1, 1, 0),
            (1, 8, 7),
            (1, 10, 9),
            (1, 15, 14),
        ])

    def test_posisi_multiline(self) -> None:
        toks = _lex("SELECT *\nFROM t")
        self.assertEqual((toks[2].line, toks[2].col, toks[2].pos), (2, 1, 9))
        self.assertEqual((toks[3].line, toks[3].col), (2, 6))

    def test_posisi_eof(self) -> None:
        eof = _lex("ab")[-1]
        self.assertEqual((eof.line, eof.col, eof.pos), (1, 3, 2))

    def test_error_di_baris_kedua(self) -> None:
        with self.assertRaises(QuerySyntaxError) as ctx:
            Lexer().tokenize("SELECT *\nFROM 'abc")
        self.assertIn("baris 2 kolom 6", str(ctx.exception))
        self.assertEqual(ctx.exception.position, 14)


class TestTokenStream(unittest.TestCase):
    def test_peek_tidak_maju(self) -> None:
        stream = Lexer().tokenize("a b")
        self.assertEqual(stream.peek().value, "a")
        self.assertEqual(stream.peek().value, "a")

    def test_peek_offset(self) -> None:
        stream = Lexer().tokenize("a b")
        self.assertEqual(stream.peek(1).value, "b")

    def test_peek_lewat_akhir_tetap_eof(self) -> None:
        stream = Lexer().tokenize("a")
        self.assertEqual(stream.peek(5).type, T.EOF)

    def test_advance_di_eof_tidak_maju(self) -> None:
        stream = Lexer().tokenize("")
        stream.advance()
        self.assertEqual(stream.advance().type, T.EOF)
        self.assertTrue(stream.is_at_end())

    def test_stream_kosong_mengembalikan_eof(self) -> None:
        stream = TokenStream()
        self.assertEqual(stream.peek().type, T.EOF)
        self.assertTrue(stream.is_at_end())

    def test_match_tipe_saja(self) -> None:
        stream = Lexer().tokenize("student")
        self.assertTrue(stream.match(T.IDENT))
        self.assertFalse(stream.match(T.KEYWORD))

    def test_match_keyword_tidak_case_sensitive(self) -> None:
        stream = Lexer().tokenize("SELECT")
        self.assertTrue(stream.match(T.KEYWORD, "select"))

    def test_match_nilai_berbeda(self) -> None:
        stream = Lexer().tokenize("SELECT")
        self.assertFalse(stream.match(T.KEYWORD, "FROM"))

    def test_match_tidak_maju(self) -> None:
        stream = Lexer().tokenize("SELECT")
        stream.match(T.KEYWORD, "SELECT")
        self.assertEqual(stream.peek().value, "SELECT")

    def test_expect_berhasil_maju(self) -> None:
        stream = Lexer().tokenize("SELECT *")
        tok = stream.expect(T.KEYWORD, "SELECT")
        self.assertEqual(tok.value, "SELECT")
        self.assertEqual(stream.peek().value, "*")

    def test_expect_gagal(self) -> None:
        stream = Lexer().tokenize("FROM")
        with self.assertRaises(QuerySyntaxError) as ctx:
            stream.expect(T.KEYWORD, "SELECT")
        self.assertIn("diharapkan SELECT", str(ctx.exception))
        self.assertEqual(ctx.exception.token, "FROM")

    def test_error_di_eof(self) -> None:
        stream = Lexer().tokenize("")
        with self.assertRaises(QuerySyntaxError) as ctx:
            stream.error("query kosong")
        self.assertIn("akhir query", str(ctx.exception))

    def test_error_dengan_token_tertentu(self) -> None:
        stream = Lexer().tokenize("SELECT x")
        tok = stream.peek(1)
        with self.assertRaises(QuerySyntaxError) as ctx:
            stream.error("contoh", tok)
        self.assertIn("baris 1 kolom 8 dekat 'x'", str(ctx.exception))
        self.assertEqual(ctx.exception.position, 7)


if __name__ == "__main__":
    unittest.main()
