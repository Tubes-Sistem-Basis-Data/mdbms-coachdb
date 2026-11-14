"""QO-17: CREATE/DROP TABLE, LIMIT, AS, dan subquery (parser sampai binder dan cost model)."""

from __future__ import annotations

import unittest
from unittest.mock import create_autospec

from common.catalog import ColumnSchema, DataType, ForeignKey, IndexInfo, TableSchema, TableStats
from common.errors import QuerySyntaxError, SemanticError
from common.expression import ColumnRef, Comparison, CompOp, Literal, Subquery
from common.interfaces import StorageManager
from common.plan import (
    CreateTable, DropTable, Filter, IndexScan, Join, JoinKind, Limit, ParsedQuery, Project, QueryKind, Sort,
    SubqueryScan, TableScan,
)
from query_optimizer.binder import Binder
from query_optimizer.cost_model import CostModel
from query_optimizer.lexer import Lexer
from query_optimizer.parser_dispatcher import ParserDispatcher
from query_optimizer.projection_rewriter import ProjectionRewriter
from query_optimizer.selection_rewriter import SelectionRewriter


def _parse(sql: str) -> ParsedQuery:
    return ParserDispatcher().parse(Lexer().tokenize(sql), sql)


class _SyntaxCase(unittest.TestCase):
    def gagal(self, sql: str, potongan: str) -> QuerySyntaxError:
        with self.assertRaises(QuerySyntaxError) as ctx:
            _parse(sql)
        self.assertIn(potongan, str(ctx.exception))
        return ctx.exception


class TestCreateTableParser(_SyntaxCase):
    def test_semua_tipe_dan_primary_key_kolom(self) -> None:
        hasil = _parse("CREATE TABLE dosen (id INTEGER PRIMARY KEY, gaji FLOAT, kode CHAR(4), nama VARCHAR(30));")
        self.assertEqual(hasil.kind, QueryKind.CREATE_TABLE)
        self.assertIsInstance(hasil.plan, CreateTable)
        schema = hasil.plan.schema
        self.assertEqual(schema.name, "dosen")
        self.assertEqual(schema.columns, [
            ColumnSchema("id", DataType.INTEGER, nullable=False),
            ColumnSchema("gaji", DataType.FLOAT),
            ColumnSchema("kode", DataType.CHAR, length=4),
            ColumnSchema("nama", DataType.VARCHAR, length=30),
        ])
        self.assertEqual(schema.primary_key, ["id"])
        self.assertEqual(schema.foreign_keys, [])

    def test_tipe_tidak_peka_huruf(self) -> None:
        schema = _parse("CREATE TABLE t (a integer, b Varchar(5), c int)").plan.schema
        self.assertEqual([c.type for c in schema.columns], [DataType.INTEGER, DataType.VARCHAR, DataType.INTEGER])

    def test_primary_key_komposit_dan_foreign_key(self) -> None:
        schema = _parse(
            "CREATE TABLE ambil (sid INTEGER, cid INTEGER REFERENCES course(id), nilai CHAR(2), "
            "PRIMARY KEY (sid, cid), FOREIGN KEY (sid) REFERENCES student(id))"
        ).plan.schema
        self.assertEqual(schema.primary_key, ["sid", "cid"])
        self.assertEqual(schema.foreign_keys, [
            ForeignKey("cid", "course", "id"),
            ForeignKey("sid", "student", "id"),
        ])
        self.assertEqual([c.nullable for c in schema.columns], [False, False, True])

    def test_create_index_tetap_dikenali(self) -> None:
        self.assertEqual(_parse("CREATE INDEX idx ON student(id)").kind, QueryKind.CREATE_INDEX)

    def test_error_sintaks(self) -> None:
        kasus = [
            ("CREATE TABLE t ()", "minimal punya satu kolom"),
            ("CREATE TABLE t (a TEXT)", "diharapkan tipe"),
            ("CREATE TABLE t (a CHAR)", "diharapkan ("),
            ("CREATE TABLE t (a VARCHAR(0))", "lebih dari 0"),
            ("CREATE TABLE t (a INTEGER PRIMARY KEY, b INTEGER PRIMARY KEY)", "lebih dari sekali"),
            ("CREATE TABLE t (a INTEGER PRIMARY KEY, PRIMARY KEY (a))", "lebih dari sekali"),
            ("CREATE TABLE t (a INTEGER, FOREIGN KEY (a, b) REFERENCES s(x))", "satu kolom"),
            ("CREATE TABLE t (a INTEGER REFERENCES s)", "diharapkan ("),
            ("CREATE TABLE t (a INTEGER", "diharapkan )"),
            ("CREATE TABLE t (PRIMARY KEY (a))", "minimal punya satu kolom"),
        ]
        for sql, potongan in kasus:
            with self.subTest(sql=sql):
                self.gagal(sql, potongan)


class TestDropTableParser(_SyntaxCase):
    def test_opsi(self) -> None:
        kasus = [
            ("DROP TABLE student", False),
            ("DROP TABLE student CASCADE;", True),
            ("drop table student restrict", False),
        ]
        for sql, cascade in kasus:
            with self.subTest(sql=sql):
                hasil = _parse(sql)
                self.assertEqual(hasil.kind, QueryKind.DROP_TABLE)
                self.assertEqual(hasil.plan, DropTable(table="student", cascade=cascade))

    def test_error(self) -> None:
        self.gagal("DROP student", "diharapkan TABLE")
        err = self.gagal("DROP TABLE student CASCADE RESTRICT", "token tidak terduga")
        self.assertEqual(err.token, "RESTRICT")


class TestLimitAliasParser(_SyntaxCase):
    def test_limit_membungkus_project(self) -> None:
        plan = _parse("SELECT * FROM student ORDER BY gpa DESC LIMIT 3;").plan
        self.assertIsInstance(plan, Limit)
        self.assertEqual(plan.count, 3)
        self.assertIsInstance(plan.children[0], Project)
        self.assertIsInstance(plan.children[0].children[0], Sort)

    def test_limit_tidak_valid(self) -> None:
        self.gagal("SELECT * FROM student LIMIT", "diharapkan INTEGER")
        self.gagal("SELECT * FROM student LIMIT -1", "diharapkan INTEGER")
        self.gagal("SELECT * FROM student LIMIT 2.5", "diharapkan INTEGER")
        self.gagal("SELECT * FROM student LIMIT 1 WHERE id = 1", "token tidak terduga")

    def test_alias_tabel_dengan_dan_tanpa_as(self) -> None:
        join = _parse("SELECT * FROM student AS s, course c").plan.children[0]
        self.assertEqual(join.children, [
            TableScan(table="student", alias="s"),
            TableScan(table="course", alias="c"),
        ])

    def test_alias_pada_join(self) -> None:
        join = _parse("SELECT * FROM student s JOIN enrollment AS e ON s.id = e.student_id").plan.children[0]
        self.assertEqual(join.kind, JoinKind.THETA)
        self.assertEqual([c.alias for c in join.children], ["s", "e"])

    def test_alias_kolom(self) -> None:
        plan = _parse("SELECT s.name AS nama, gpa FROM student s").plan
        self.assertEqual(plan.columns, [ColumnRef("s", "name"), ColumnRef(None, "gpa")])
        self.assertEqual(plan.aliases, ["nama", None])

    def test_tanpa_alias_kolom_list_kosong(self) -> None:
        self.assertEqual(_parse("SELECT name, gpa FROM student").plan.aliases, [])

    def test_as_tanpa_nama(self) -> None:
        self.gagal("SELECT name AS FROM student", "diharapkan IDENT")
        self.gagal("SELECT * FROM student AS", "diharapkan IDENT")


class TestSubqueryParser(_SyntaxCase):
    def test_subquery_di_where(self) -> None:
        plan = _parse("SELECT name FROM student WHERE gpa > (SELECT gpa FROM student WHERE id = 1)").plan
        cond = plan.children[0].condition
        self.assertIsInstance(cond, Comparison)
        self.assertIsInstance(cond.right, Subquery)
        self.assertIsInstance(cond.right.plan, Project)
        self.assertIsInstance(cond.right.plan.children[0], Filter)

    def test_subquery_di_where_update_dan_delete(self) -> None:
        for sql in (
            "DELETE FROM student WHERE gpa < (SELECT gpa FROM student LIMIT 1)",
            "UPDATE student SET gpa = 4.0 WHERE id = (SELECT id FROM student LIMIT 1)",
        ):
            with self.subTest(sql=sql):
                cond = _parse(sql).plan.children[0].condition
                self.assertIsInstance(cond.right, Subquery)

    def test_kurung_biasa_tetap_ekspresi(self) -> None:
        cond = _parse("SELECT * FROM student WHERE (gpa + 1) > 3").plan.children[0].condition
        self.assertNotIsInstance(cond.left, Subquery)

    def test_subquery_di_from(self) -> None:
        plan = _parse("SELECT t.id FROM (SELECT id FROM student) AS t").plan
        scan = plan.children[0]
        self.assertIsInstance(scan, SubqueryScan)
        self.assertEqual(scan.alias, "t")
        self.assertIsInstance(scan.children[0], Project)

    def test_subquery_error(self) -> None:
        self.gagal("SELECT * FROM (SELECT id FROM student)", "wajib memakai alias")
        self.gagal("SELECT * FROM (student) AS t", "diharapkan SELECT")
        self.gagal("SELECT * FROM student WHERE gpa > (SELECT gpa FROM student", "diharapkan )")


class _CatalogCase(unittest.TestCase):
    def setUp(self) -> None:
        self.schemas = {
            "student": TableSchema("student", [
                ColumnSchema("id", DataType.INTEGER, nullable=False),
                ColumnSchema("name", DataType.VARCHAR, length=10),
                ColumnSchema("gpa", DataType.FLOAT),
            ], primary_key=["id"]),
            "course": TableSchema("course", [
                ColumnSchema("id", DataType.INTEGER, nullable=False),
                ColumnSchema("title", DataType.VARCHAR, length=30),
            ], primary_key=["id"]),
        }
        self.stats = {
            "student": TableStats("student", 1000, 100, 30.0, 10, {"id": 1000, "name": 900, "gpa": 50}),
            "course": TableStats("course", 50, 5, 40.0, 10, {"id": 50, "title": 50}),
        }
        self.sm = create_autospec(StorageManager, instance=True, spec_set=True)

        def get_schema(table: str) -> TableSchema:
            if table not in self.schemas:
                raise SemanticError(f"Tabel '{table}' tidak ada", table)
            return self.schemas[table]

        self.sm.get_schema.side_effect = get_schema
        self.sm.get_stats.side_effect = self.stats.__getitem__
        self.binder = Binder(self.sm)
        self.model = CostModel(self.sm, buffer_frames=12, block_size=1000)

    def bind(self, sql: str) -> ParsedQuery:
        return self.binder.bind(_parse(sql))

    def tolak(self, sql: str, potongan: str) -> None:
        with self.assertRaises(SemanticError) as ctx:
            self.bind(sql)
        self.assertIn(potongan, str(ctx.exception))


class TestBindAlias(_CatalogCase):
    def test_kolom_memakai_alias(self) -> None:
        plan = self.bind("SELECT s.name, gpa FROM student AS s WHERE s.id = 1").plan
        self.assertEqual(plan.columns, [ColumnRef("s", "name"), ColumnRef("s", "gpa")])
        self.assertEqual(plan.children[0].condition.left, ColumnRef("s", "id"))

    def test_nama_asli_tidak_berlaku_setelah_alias(self) -> None:
        self.tolak("SELECT student.name FROM student AS s", "tidak ditemukan")

    def test_self_join_dengan_alias(self) -> None:
        plan = self.bind("SELECT a.name, b.name FROM student a, student b WHERE a.gpa > b.gpa").plan
        self.assertEqual(plan.columns, [ColumnRef("a", "name"), ColumnRef("b", "name")])

    def test_alias_sama_ditolak(self) -> None:
        self.tolak("SELECT * FROM student s, course s", "Tabel berulang")

    def test_alias_kolom_dicek(self) -> None:
        with self.assertRaisesRegex(SemanticError, "SELECT \\*"):
            self.binder.bind(ParsedQuery("x", QueryKind.SELECT, plan=Project(
                columns=[], aliases=["a"], children=[TableScan(table="student")])))
        with self.assertRaisesRegex(SemanticError, "Jumlah alias"):
            self.binder.bind(ParsedQuery("x", QueryKind.SELECT, plan=Project(
                columns=[ColumnRef(None, "id")], aliases=["a", "b"], children=[TableScan(table="student")])))

    def test_index_scan_dengan_alias(self) -> None:
        index = IndexInfo("idx_id", "student", "id")
        self.schemas["student"].indexes.append(index)
        scan = IndexScan(table="student", index=index, op=CompOp.EQ, key=1, alias="s")
        plan = Project(columns=[ColumnRef("s", "name")], children=[scan])
        self.binder.bind(ParsedQuery("x", QueryKind.SELECT, plan=plan))
        self.assertEqual(plan.output_columns, [ColumnRef("s", "name")])
        self.assertGreater(self.model.get_cost(plan), 0)

    def test_limit(self) -> None:
        plan = self.bind("SELECT name FROM student LIMIT 2").plan
        self.assertEqual(plan.output_columns, [ColumnRef("student", "name")])
        with self.assertRaisesRegex(SemanticError, "LIMIT"):
            self.binder.bind(ParsedQuery("x", QueryKind.SELECT, plan=Limit(count=-1, children=[
                Project(columns=[], children=[TableScan(table="student")])])))


class TestBindSubquery(_CatalogCase):
    def test_subquery_skalar(self) -> None:
        plan = self.bind("SELECT name FROM student WHERE gpa > (SELECT gpa FROM student WHERE id = 1)").plan
        inner = plan.children[0].condition.right.plan
        self.assertEqual(inner.columns, [ColumnRef("student", "gpa")])

    def test_subquery_harus_satu_kolom(self) -> None:
        self.tolak("SELECT name FROM student WHERE gpa > (SELECT id, gpa FROM student)", "tepat satu kolom")
        self.tolak("SELECT name FROM student WHERE gpa > (SELECT * FROM student)", "tepat satu kolom")

    def test_tipe_subquery_dicek(self) -> None:
        self.tolak("SELECT name FROM student WHERE gpa > (SELECT title FROM course LIMIT 1)", "tidak kompatibel")

    def test_subquery_berkorelasi_ditolak(self) -> None:
        self.tolak("SELECT name FROM student s WHERE gpa > (SELECT id FROM course WHERE course.id = s.id)",
                   "tidak ditemukan")

    def test_subquery_dalam_aritmetika_ditolak(self) -> None:
        self.tolak("SELECT name FROM student WHERE gpa > (SELECT gpa FROM student LIMIT 1) + 1", "operand langsung")

    def test_subquery_di_from(self) -> None:
        plan = self.bind("SELECT t.nama FROM (SELECT name AS nama, gpa FROM student) AS t WHERE t.gpa > 3.0").plan
        self.assertEqual(plan.columns, [ColumnRef("t", "nama")])
        self.assertEqual(plan.children[0].condition.left, ColumnRef("t", "gpa"))
        self.assertEqual(plan.children[0].children[0].output_columns, [ColumnRef("t", "nama"), ColumnRef("t", "gpa")])

    def test_subquery_di_from_kolom_ganda(self) -> None:
        self.tolak("SELECT * FROM (SELECT s.id, c.id FROM student s, course c) AS t", "beri alias")
        self.bind("SELECT * FROM (SELECT s.id AS sid, c.id FROM student s, course c) AS t")

    def test_isi_subquery_tidak_terlihat_dari_luar(self) -> None:
        self.tolak("SELECT student.name FROM (SELECT name FROM student) AS t", "tidak ditemukan")


class TestBindDdl(_CatalogCase):
    def test_create_table_valid(self) -> None:
        query = self.bind(
            "CREATE TABLE ambil (sid INTEGER REFERENCES student(id), cid INTEGER, nilai CHAR(2), "
            "PRIMARY KEY (sid, cid), FOREIGN KEY (cid) REFERENCES course(id))"
        )
        self.assertEqual(query.plan.output_columns, [])

    def test_create_table_rujuk_diri_sendiri(self) -> None:
        self.bind("CREATE TABLE pegawai (id INTEGER PRIMARY KEY, atasan INTEGER REFERENCES pegawai(id))")

    def test_create_table_error(self) -> None:
        kasus = [
            ("CREATE TABLE student (id INTEGER)", "sudah ada"),
            ("CREATE TABLE t (a INTEGER, a FLOAT)", "lebih dari sekali"),
            ("CREATE TABLE t (a INTEGER, PRIMARY KEY (b))", "tidak ada di tabel"),
            ("CREATE TABLE t (a INTEGER, PRIMARY KEY (a, a))", "lebih dari sekali"),
            ("CREATE TABLE t (a INTEGER, FOREIGN KEY (b) REFERENCES student(id))", "tidak ada di tabel"),
            ("CREATE TABLE t (a INTEGER REFERENCES dosen(id))", "tidak ada"),
            ("CREATE TABLE t (a INTEGER REFERENCES student(nip))", "tidak ditemukan"),
            ("CREATE TABLE t (a FLOAT REFERENCES student(id))", "Tipe kolom foreign key"),
        ]
        for sql, potongan in kasus:
            with self.subTest(sql=sql):
                self.tolak(sql, potongan)

    def test_primary_key_dijadikan_not_null(self) -> None:
        schema = TableSchema("t", [ColumnSchema("a", DataType.INTEGER)], primary_key=["a"])
        self.binder.bind(ParsedQuery("x", QueryKind.CREATE_TABLE, plan=CreateTable(schema=schema)))
        self.assertFalse(schema.columns[0].nullable)

    def test_drop_table(self) -> None:
        self.bind("DROP TABLE course CASCADE")
        self.tolak("DROP TABLE dosen", "tidak ada")


class TestCostExtra(_CatalogCase):
    def cost(self, sql: str) -> tuple[ParsedQuery, float]:
        query = self.bind(sql)
        return query, self.model.get_cost(query.plan)

    def test_limit_membatasi_baris(self) -> None:
        query, cost = self.cost("SELECT * FROM student LIMIT 5")
        self.assertEqual(query.plan.estimated_rows, 5)
        self.assertEqual(cost, 100)

    def test_alias_bisa_diestimasi(self) -> None:
        _, cost = self.cost("SELECT a.name FROM student a, student b WHERE a.id = b.id")
        self.assertGreater(cost, 0)

    def test_subquery_menambah_biaya_sekali(self) -> None:
        _, biasa = self.cost("SELECT name FROM student WHERE gpa > 3.0")
        _, sub = self.cost("SELECT name FROM student WHERE gpa > (SELECT id FROM course LIMIT 1)")
        self.assertEqual(sub, biasa + 5)

    def test_subquery_di_from(self) -> None:
        query, cost = self.cost("SELECT t.nama FROM (SELECT name AS nama FROM student) AS t WHERE t.nama = 'a'")
        self.assertEqual(cost, 100)
        self.assertAlmostEqual(query.plan.estimated_rows, 1000 / 900)

    def test_ddl_tanpa_biaya(self) -> None:
        self.assertEqual(self.model.get_cost(_parse("DROP TABLE course").plan), 0)
        self.assertEqual(self.model.get_cost(_parse("CREATE TABLE t (a INTEGER)").plan), 0)


class TestRewriteExtra(_CatalogCase):
    def test_seleksi_turun_ke_sisi_alias(self) -> None:
        plan = self.bind("SELECT * FROM student a, student b WHERE a.id = 1").plan
        hasil = SelectionRewriter().apply(plan)
        join = hasil.children[0]
        self.assertIsInstance(join, Join)
        self.assertIsInstance(join.children[0], Filter)
        self.assertEqual(join.children[0].condition.left, ColumnRef("a", "id"))
        self.assertIsInstance(join.children[1], TableScan)

    def test_seleksi_turun_ke_sisi_subquery_tanpa_masuk(self) -> None:
        plan = self.bind("SELECT * FROM course, (SELECT id FROM student LIMIT 3) AS t WHERE t.id = 1").plan
        join = SelectionRewriter().apply(plan).children[0]
        self.assertIsInstance(join.children[1], Filter)
        self.assertIsInstance(join.children[1].children[0], SubqueryScan)
        # filter tidak boleh melewati LIMIT di dalam subquery
        self.assertIsInstance(join.children[1].children[0].children[0], Limit)

    def test_proyeksi_dengan_alias_tetap_benar(self) -> None:
        plan = self.bind("SELECT a.name AS nama FROM student a, course c WHERE a.id = c.id").plan
        hasil = ProjectionRewriter().apply(plan)
        self.assertEqual(hasil.columns, [ColumnRef("a", "name")])
        self.assertEqual(hasil.aliases, ["nama"])


if __name__ == "__main__":
    unittest.main()
