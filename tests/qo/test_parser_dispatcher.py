"""Uji ParserDispatcher (QO-02): pemilihan parser, ';' dan EOF, serta EXPLAIN.

SELECT memakai SelectParser asli. Perintah lain memakai StatementParser palsu supaya
yang diuji hanya dispatcher, bukan isi parser DML/transaksi.
"""

import unittest

from common.errors import QuerySyntaxError
from common.plan import Filter, ParsedQuery, Project, QueryKind, TableScan, TransactionCommand
from query_optimizer.lexer import Lexer, TokenStream, TokenType
from query_optimizer.parser_dispatcher import ParserDispatcher

T = TokenType


class _FakeStatementParser:
    """Memakan token sampai ';' atau EOF, lalu mencatat metode yang dipanggil."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def _consume(self, tokens: TokenStream) -> list[str]:
        nilai = []
        while not tokens.match(T.SEMICOLON) and not tokens.is_at_end():
            nilai.append(str(tokens.advance().value))
        return nilai

    def parse_update(self, tokens: TokenStream) -> ParsedQuery:
        self.calls.append("update")
        self._consume(tokens)
        return ParsedQuery(sql="", kind=QueryKind.UPDATE)

    def parse_insert(self, tokens: TokenStream) -> ParsedQuery:
        self.calls.append("insert")
        self._consume(tokens)
        return ParsedQuery(sql="", kind=QueryKind.INSERT)

    def parse_delete(self, tokens: TokenStream) -> ParsedQuery:
        self.calls.append("delete")
        self._consume(tokens)
        return ParsedQuery(sql="", kind=QueryKind.DELETE)

    def parse_create_index(self, tokens: TokenStream) -> ParsedQuery:
        self.calls.append("create_index")
        self._consume(tokens)
        return ParsedQuery(sql="", kind=QueryKind.CREATE_INDEX)

    def parse_create_table(self, tokens: TokenStream) -> ParsedQuery:
        self.calls.append("create_table")
        self._consume(tokens)
        return ParsedQuery(sql="", kind=QueryKind.CREATE_TABLE)

    def parse_drop_table(self, tokens: TokenStream) -> ParsedQuery:
        self.calls.append("drop_table")
        self._consume(tokens)
        return ParsedQuery(sql="", kind=QueryKind.DROP_TABLE)

    def parse_transaction(self, tokens: TokenStream) -> ParsedQuery:
        self.calls.append("transaction")
        kata = self._consume(tokens)[0]
        return ParsedQuery(sql="", kind=QueryKind.TRANSACTION, command=TransactionCommand[kata])


class _DispatcherTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.dispatcher = ParserDispatcher()
        self.stmt = _FakeStatementParser()
        self.dispatcher._statement_parser = self.stmt

    def parse(self, sql: str) -> ParsedQuery:
        return self.dispatcher.parse(Lexer().tokenize(sql), sql)

    def assert_error(self, sql: str, pesan: str) -> QuerySyntaxError:
        with self.assertRaises(QuerySyntaxError) as ctx:
            self.parse(sql)
        self.assertIn(pesan, ctx.exception.message)
        return ctx.exception


class TestPemilihanParser(_DispatcherTestCase):
    def test_select_ke_select_parser(self) -> None:
        hasil = self.parse("SELECT * FROM student;")
        self.assertEqual(hasil.kind, QueryKind.SELECT)
        self.assertIsInstance(hasil.plan, Project)
        self.assertEqual(hasil.plan.children[0], TableScan(table="student"))
        self.assertEqual(self.stmt.calls, [])

    def test_select_dengan_where(self) -> None:
        hasil = self.parse("SELECT * FROM student WHERE id = 1;")
        self.assertIsInstance(hasil.plan.children[0], Filter)

    def test_dml_dan_ddl_ke_statement_parser(self) -> None:
        kasus = [
            ("UPDATE student SET gpa = 4.0;", "update", QueryKind.UPDATE),
            ("INSERT INTO student VALUES (1);", "insert", QueryKind.INSERT),
            ("DELETE FROM student;", "delete", QueryKind.DELETE),
            ("CREATE INDEX idx ON student(id);", "create_index", QueryKind.CREATE_INDEX),
        ]
        for sql, metode, kind in kasus:
            with self.subTest(sql=sql):
                self.stmt.calls.clear()
                self.assertEqual(self.parse(sql).kind, kind)
                self.assertEqual(self.stmt.calls, [metode])

    def test_transaksi_ke_parse_transaction(self) -> None:
        for kata in ["BEGIN", "COMMIT", "ABORT"]:
            with self.subTest(kata=kata):
                self.stmt.calls.clear()
                hasil = self.parse(f"{kata};")
                self.assertEqual(self.stmt.calls, ["transaction"])
                self.assertEqual(hasil.command, TransactionCommand[kata])

    def test_keyword_huruf_kecil(self) -> None:
        self.assertEqual(self.parse("select * from student").kind, QueryKind.SELECT)
        self.assertEqual(self.parse("commit;").command, TransactionCommand.COMMIT)

    def test_create_table_dan_create_index(self) -> None:
        self.assertEqual(self.parse("CREATE TABLE t (a INTEGER);").kind, QueryKind.CREATE_TABLE)
        self.assertEqual(self.parse("CREATE INDEX i ON t(a);").kind, QueryKind.CREATE_INDEX)
        self.assertEqual(self.stmt.calls, ["create_table", "create_index"])

    def test_drop_table(self) -> None:
        self.assertEqual(self.parse("DROP TABLE t;").kind, QueryKind.DROP_TABLE)
        self.assertEqual(self.stmt.calls, ["drop_table"])

    def test_keyword_bukan_awal_perintah(self) -> None:
        self.assert_error("FROM student;", "pernyataan tidak dikenal")

    def test_identifier_di_awal(self) -> None:
        err = self.assert_error("TRUNCATE student;", "pernyataan tidak dikenal")
        self.assertEqual(err.token, "TRUNCATE")
        self.assertEqual(err.position, 0)


class TestAkhirQuery(_DispatcherTestCase):
    def test_titik_koma_opsional(self) -> None:
        self.assertEqual(self.parse("SELECT * FROM student").kind, QueryKind.SELECT)
        self.assertEqual(self.parse("COMMIT").command, TransactionCommand.COMMIT)

    def test_whitespace_setelah_titik_koma(self) -> None:
        self.assertEqual(self.parse("SELECT * FROM student;  \n ").kind, QueryKind.SELECT)

    def test_query_kosong(self) -> None:
        self.assert_error("", "query kosong")
        self.assert_error("   \n  ", "query kosong")

    def test_titik_koma_saja(self) -> None:
        self.assert_error(";", "pernyataan tidak dikenal")

    def test_titik_koma_ganda(self) -> None:
        self.assert_error("SELECT * FROM student;;", "token tidak terduga setelah akhir query")

    def test_dua_query_sekaligus(self) -> None:
        err = self.assert_error("SELECT * FROM a; SELECT * FROM b;", "token tidak terduga setelah akhir query")
        self.assertEqual(err.token, "SELECT")

    def test_sisa_token_tanpa_titik_koma(self) -> None:
        # 'b' dibaca sebagai alias tabel, jadi sisa token dimulai dari 'c'
        err = self.assert_error("SELECT * FROM a b c", "token tidak terduga setelah akhir query")
        self.assertEqual(err.token, "c")


class TestSql(_DispatcherTestCase):
    def test_sql_diisi_teks_asli(self) -> None:
        sql = "SELECT * FROM student;"
        self.assertEqual(self.parse(sql).sql, sql)

    def test_sql_diisi_untuk_statement(self) -> None:
        self.assertEqual(self.parse("BEGIN TRANSACTION;").sql, "BEGIN TRANSACTION;")


class TestExplain(_DispatcherTestCase):
    def test_explain_select(self) -> None:
        sql = "EXPLAIN SELECT * FROM student WHERE id = 1;"
        hasil = self.parse(sql)
        biasa = self.parse("SELECT * FROM student WHERE id = 1;")
        self.assertEqual(hasil.kind, QueryKind.EXPLAIN)
        self.assertEqual(hasil.plan, biasa.plan)
        self.assertEqual(hasil.sql, sql)
        self.assertIsNone(hasil.command)

    def test_explain_statement(self) -> None:
        hasil = self.parse("EXPLAIN DELETE FROM student;")
        self.assertEqual(hasil.kind, QueryKind.EXPLAIN)
        self.assertEqual(self.stmt.calls, ["delete"])

    def test_explain_huruf_kecil_tanpa_titik_koma(self) -> None:
        self.assertEqual(self.parse("explain select * from student").kind, QueryKind.EXPLAIN)

    def test_explain_tanpa_query(self) -> None:
        self.assert_error("EXPLAIN", "query kosong")
        self.assert_error("EXPLAIN;", "pernyataan tidak dikenal")

    def test_explain_bertumpuk(self) -> None:
        self.assert_error("EXPLAIN EXPLAIN SELECT * FROM student;", "EXPLAIN tidak boleh bertumpuk")

    def test_explain_sisa_token(self) -> None:
        self.assert_error("EXPLAIN SELECT * FROM a; x", "token tidak terduga setelah akhir query")


if __name__ == "__main__":
    unittest.main()
