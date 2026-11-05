import unittest

from common.catalog import IndexInfo, IndexKind
from common.errors import QuerySyntaxError
from common.plan import CreateIndex, QueryKind, TransactionCommand
from query_optimizer.lexer import Lexer, TokenType
from query_optimizer.statement_parser import StatementParser

T = TokenType


def _transaksi(sql: str):
    stream = Lexer().tokenize(sql)
    return StatementParser().parse_transaction(stream), stream


def _index(sql: str):
    stream = Lexer().tokenize(sql)
    return StatementParser().parse_create_index(stream), stream


def _gagal(test: unittest.TestCase, parse, sql: str, potongan: str = "") -> None:
    with test.assertRaises(QuerySyntaxError) as ctx:
        parse(sql)
    test.assertIn("Kesalahan sintaks", str(ctx.exception))
    test.assertIn(potongan, str(ctx.exception))


class TestTransaction(unittest.TestCase):
    def test_begin(self) -> None:
        hasil, stream = _transaksi("BEGIN;")
        self.assertEqual(hasil.kind, QueryKind.TRANSACTION)
        self.assertEqual(hasil.command, TransactionCommand.BEGIN)
        self.assertEqual(hasil.sql, "")
        self.assertIsNone(hasil.plan)
        self.assertTrue(stream.match(T.SEMICOLON))

    def test_begin_transaction(self) -> None:
        hasil, stream = _transaksi("BEGIN TRANSACTION;")
        self.assertEqual(hasil.command, TransactionCommand.BEGIN)
        self.assertTrue(stream.match(T.SEMICOLON))

    def test_commit(self) -> None:
        hasil, _ = _transaksi("COMMIT;")
        self.assertEqual(hasil.command, TransactionCommand.COMMIT)
        self.assertIsNone(hasil.plan)

    def test_abort(self) -> None:
        hasil, _ = _transaksi("ABORT")
        self.assertEqual(hasil.command, TransactionCommand.ABORT)

    def test_tidak_case_sensitive(self) -> None:
        hasil, _ = _transaksi("begin transaction")
        self.assertEqual(hasil.command, TransactionCommand.BEGIN)

    def test_transaction_setelah_commit_tidak_dikonsumsi(self) -> None:
        _, stream = _transaksi("COMMIT TRANSACTION")
        self.assertTrue(stream.match(T.KEYWORD, "TRANSACTION"))

    def test_gagal_keyword_lain(self) -> None:
        _gagal(self, _transaksi, "ROLLBACK", "BEGIN, COMMIT, atau ABORT")

    def test_gagal_kosong(self) -> None:
        _gagal(self, _transaksi, "", "BEGIN, COMMIT, atau ABORT")


class TestCreateIndex(unittest.TestCase):
    def test_default_btree(self) -> None:
        hasil, stream = _index("CREATE INDEX idx_gpa ON student(gpa);")
        self.assertEqual(hasil.kind, QueryKind.CREATE_INDEX)
        self.assertEqual(hasil.sql, "")
        self.assertEqual(hasil.plan, CreateIndex(
            index=IndexInfo(name="idx_gpa", table="student", column="gpa", kind=IndexKind.BPLUS_TREE)))
        self.assertTrue(stream.match(T.SEMICOLON))

    def test_using_btree(self) -> None:
        hasil, _ = _index("CREATE INDEX i ON t(c) USING BTREE")
        self.assertEqual(hasil.plan.index.kind, IndexKind.BPLUS_TREE)

    def test_using_hash(self) -> None:
        hasil, _ = _index("CREATE INDEX i ON t(c) USING HASH;")
        self.assertEqual(hasil.plan.index.kind, IndexKind.HASH)

    def test_tidak_case_sensitive_keyword_saja(self) -> None:
        hasil, _ = _index("create index Idx_Name on Student(Name) using hash")
        self.assertEqual(hasil.plan.index,
                         IndexInfo(name="Idx_Name", table="Student", column="Name", kind=IndexKind.HASH))

    def test_spasi_sebelum_kurung(self) -> None:
        hasil, _ = _index("CREATE INDEX i ON t ( c )")
        self.assertEqual(hasil.plan.index.column, "c")

    def test_gagal_bukan_index(self) -> None:
        _gagal(self, _index, "CREATE TABLE t", "INDEX")

    def test_gagal_tanpa_nama(self) -> None:
        _gagal(self, _index, "CREATE INDEX ON t(c)", "IDENT")

    def test_gagal_tanpa_on(self) -> None:
        _gagal(self, _index, "CREATE INDEX i t(c)", "ON")

    def test_gagal_tanpa_kolom(self) -> None:
        _gagal(self, _index, "CREATE INDEX i ON t()", "IDENT")

    def test_gagal_kurung_tidak_ditutup(self) -> None:
        _gagal(self, _index, "CREATE INDEX i ON t(c", ")")

    def test_gagal_tanpa_kurung(self) -> None:
        _gagal(self, _index, "CREATE INDEX i ON t c", "(")

    def test_gagal_banyak_kolom(self) -> None:
        _gagal(self, _index, "CREATE INDEX i ON t(a, b)", ")")

    def test_gagal_jenis_tidak_dikenal(self) -> None:
        _gagal(self, _index, "CREATE INDEX i ON t(c) USING RTREE", "BTREE atau HASH")

    def test_gagal_using_tanpa_jenis(self) -> None:
        _gagal(self, _index, "CREATE INDEX i ON t(c) USING", "BTREE atau HASH")


if __name__ == "__main__":
    unittest.main()