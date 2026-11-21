"""Uji end-to-end bonus QO-17 (CREATE/DROP TABLE, LIMIT, AS, subquery) lintas QO, QP, SM, CCM, FRM."""

from __future__ import annotations

import unittest
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

from common.plan import ParsedQuery
from common.result import ExecutionResult
from concurrency_control.manager import CCManager
from failure_recovery import FailureRecoveryManager
from failure_recovery.log import codec
from query_optimizer import QueryOptimizerFacade
from query_processor.facade import QueryProcessorFacade
from storage_manager.facade import StorageManager


class _QOTanpaOptimasi(QueryOptimizerFacade):
    def optimize_query(self, query: ParsedQuery) -> ParsedQuery:
        return query


class _BonusE2ECase(unittest.TestCase):
    def setUp(self) -> None:
        previous_path = codec.get_log_path()
        self.addCleanup(codec.set_log_path, previous_path)
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)

        self.sm = StorageManager(data_dir=temp.name)
        self.addCleanup(self.sm.close)
        self.frm = FailureRecoveryManager(log_path=str(Path(temp.name) / "wal.log"))
        self.addCleanup(self.frm.close)
        self.ccm = CCManager(self.frm)
        self.frm.set_dependencies(self.sm, self.ccm)
        self.sm.set_log_flusher(self.frm.flush_log)

        self.qo = QueryOptimizerFacade(self.sm)
        self.qp = QueryProcessorFacade(self.qo, self.sm, self.ccm, self.frm)
        self.qp_tanpa_optimasi = QueryProcessorFacade(
            _QOTanpaOptimasi(self.sm), self.sm, self.ccm, self.frm
        )

    def jalankan(self, sql: str, qp: QueryProcessorFacade | None = None) -> ExecutionResult:
        target = self.qp if qp is None else qp
        session = target.open_session()
        try:
            return target.execute_query(sql, session)
        finally:
            target.close_session(session.session_id)

    def jalankan_ok(self, sql: str) -> ExecutionResult:
        res = self.jalankan(sql)
        self.assertTrue(res.ok, f"query gagal ({sql}): {res.message}")
        return res

    def assert_ekuivalen(self, sql: str, *, cek_urutan: bool = False) -> ExecutionResult:
        nyala = self.jalankan(sql, self.qp)
        mati = self.jalankan(sql, self.qp_tanpa_optimasi)
        self.assertTrue(nyala.ok, f"dengan optimasi gagal ({sql}): {nyala.message}")
        self.assertTrue(mati.ok, f"tanpa optimasi gagal ({sql}): {mati.message}")
        self.assertEqual(nyala.columns, mati.columns)
        if cek_urutan:
            self.assertEqual(nyala.rows, mati.rows)
        else:
            self.assertEqual(
                Counter(tuple(row) for row in nyala.rows),
                Counter(tuple(row) for row in mati.rows),
            )
        return nyala


class TestCreateDropTableE2E(_BonusE2ECase):
    def test_create_dml_restrict_dan_cascade_end_to_end(self) -> None:
        self.jalankan_ok(
            "CREATE TABLE dosen (id INTEGER PRIMARY KEY, nama VARCHAR(40), gaji FLOAT);"
        )
        self.jalankan_ok(
            "CREATE TABLE kelas ("
            "kode CHAR(6), semester INTEGER, dosen_id INTEGER REFERENCES dosen(id), sks INTEGER, "
            "PRIMARY KEY (kode, semester)"
            ");"
        )
        self.assertEqual(sorted(self.sm.list_tables()), ["dosen", "kelas"])

        self.jalankan_ok("INSERT INTO dosen VALUES (1, 'Andi', 10.5);")
        self.jalankan_ok("INSERT INTO dosen VALUES (2, 'Budi', 12.0);")
        self.jalankan_ok("INSERT INTO kelas VALUES ('IF101', 1, 1, 3);")
        self.jalankan_ok("INSERT INTO kelas VALUES ('IF102', 2, 2, 4);")

        hasil = self.jalankan_ok(
            "SELECT d.nama, k.kode FROM dosen d JOIN kelas k ON d.id = k.dosen_id ORDER BY d.id;"
        )
        self.assertEqual(hasil.columns, ["nama", "kode"])
        self.assertEqual(hasil.rows, [["Andi", "IF101"], ["Budi", "IF102"]])

        # DROP TABLE default (RESTRICT) dan eksplisit RESTRICT harus menolak tabel yang dirujuk FK.
        for sql in ("DROP TABLE dosen;", "DROP TABLE dosen RESTRICT;"):
            with self.subTest(sql=sql):
                ditolak = self.jalankan(sql)
                self.assertFalse(ditolak.ok)
                self.assertIn("RESTRICT", ditolak.message)
        self.assertEqual(sorted(self.sm.list_tables()), ["dosen", "kelas"])

        # DROP TABLE CASCADE harus menghapus tabel dependen beserta tabel target.
        self.jalankan_ok("DROP TABLE dosen CASCADE;")
        self.assertEqual(self.sm.list_tables(), [])

        gagal_select = self.jalankan("SELECT * FROM dosen;")
        self.assertFalse(gagal_select.ok)

    def test_error_semantik_create_dan_drop(self) -> None:
        self.jalankan_ok("CREATE TABLE ruang (id INTEGER PRIMARY KEY);")

        duplikat = self.jalankan("CREATE TABLE ruang (id INTEGER);")
        self.assertFalse(duplikat.ok)

        tidak_ada = self.jalankan("DROP TABLE tidak_ada;")
        self.assertFalse(tidak_ada.ok)

    def test_explain_ddl_tidak_mengubah_katalog(self) -> None:
        self.jalankan_ok("CREATE TABLE ruang (id INTEGER PRIMARY KEY);")

        exp_create = self.jalankan_ok("EXPLAIN CREATE TABLE sementara (x INTEGER);")
        self.assertIn("CreateTable [sementara]", exp_create.message)
        self.assertNotIn("sementara", self.sm.list_tables())

        exp_drop = self.jalankan_ok("EXPLAIN DROP TABLE ruang CASCADE;")
        self.assertIn("DropTable [ruang CASCADE]", exp_drop.message)
        self.assertIn("ruang", self.sm.list_tables())


class TestLimitAliasSubqueryE2E(_BonusE2ECase):
    def setUp(self) -> None:
        super().setUp()
        self.jalankan_ok(
            "CREATE TABLE dosen (id INTEGER PRIMARY KEY, nama VARCHAR(40), gaji FLOAT);"
        )
        self.jalankan_ok(
            "CREATE TABLE kelas (kode CHAR(6) PRIMARY KEY, dosen_id INTEGER REFERENCES dosen(id), sks INTEGER);"
        )
        for row in (
            "(1, 'Andi', 10.5)",
            "(2, 'Budi', 12.0)",
            "(3, 'Citra', 15.0)",
            "(4, 'Dina', 13.5)",
        ):
            self.jalankan_ok(f"INSERT INTO dosen VALUES {row};")
        for row in (
            "('IF101', 1, 3)",
            "('IF102', 2, 4)",
            "('IF103', 3, 3)",
        ):
            self.jalankan_ok(f"INSERT INTO kelas VALUES {row};")

    def test_limit_dan_alias_ekuivalen(self) -> None:
        hasil = self.assert_ekuivalen(
            "SELECT d.nama AS pengajar, d.gaji FROM dosen d ORDER BY d.gaji DESC LIMIT 2;",
            cek_urutan=True,
        )
        self.assertEqual(hasil.columns, ["pengajar", "gaji"])
        self.assertEqual(hasil.rows, [["Citra", 15.0], ["Dina", 13.5]])

        kosong = self.assert_ekuivalen(
            "SELECT nama AS pengajar FROM dosen ORDER BY id LIMIT 0;",
            cek_urutan=True,
        )
        self.assertEqual(kosong.columns, ["pengajar"])
        self.assertEqual(kosong.rows, [])

    def test_self_join_dengan_alias_tabel_dan_kolom(self) -> None:
        hasil = self.assert_ekuivalen(
            "SELECT a.nama AS senior, b.nama AS junior "
            "FROM dosen AS a JOIN dosen b ON a.id < b.id "
            "WHERE a.gaji > 12.0 ORDER BY a.id LIMIT 2;",
            cek_urutan=True,
        )
        self.assertEqual(hasil.columns, ["senior", "junior"])
        self.assertEqual(hasil.rows, [["Citra", "Dina"]])

    def test_subquery_di_from_dengan_join_dan_limit(self) -> None:
        hasil = self.assert_ekuivalen(
            "SELECT sub.pengajar, k.kode "
            "FROM (SELECT id, nama AS pengajar FROM dosen WHERE gaji >= 12.0) AS sub "
            "JOIN kelas k ON sub.id = k.dosen_id "
            "ORDER BY k.kode LIMIT 2;",
            cek_urutan=True,
        )
        self.assertEqual(hasil.columns, ["pengajar", "kode"])
        self.assertEqual(hasil.rows, [["Budi", "IF102"], ["Citra", "IF103"]])

    def test_scalar_subquery_di_where(self) -> None:
        hasil = self.assert_ekuivalen(
            "SELECT nama FROM dosen "
            "WHERE gaji > (SELECT gaji FROM dosen WHERE id = 2 LIMIT 1) "
            "ORDER BY gaji DESC LIMIT 2;",
            cek_urutan=True,
        )
        self.assertEqual(hasil.columns, ["nama"])
        self.assertEqual(hasil.rows, [["Citra"], ["Dina"]])

        kosong = self.assert_ekuivalen(
            "SELECT nama FROM dosen WHERE gaji > (SELECT gaji FROM dosen WHERE id = 99 LIMIT 1);",
            cek_urutan=True,
        )
        self.assertEqual(kosong.rows, [])

        lebih_dari_satu = self.jalankan(
            "SELECT nama FROM dosen WHERE gaji > (SELECT gaji FROM dosen LIMIT 2);"
        )
        self.assertFalse(lebih_dari_satu.ok)


if __name__ == "__main__":
    unittest.main()
