"""Uji ekuivalensi: hasil query sebelum dan sesudah optimasi harus sama."""
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
from query_optimizer.plan_printer import PlanPrinter
from query_processor.facade import QueryProcessorFacade
from scripts.seed_sample_db import SampleDatabaseSeeder
from storage_manager.facade import StorageManager


class QOTanpaOptimasi(QueryOptimizerFacade):
    def optimize_query(self, query: ParsedQuery) -> ParsedQuery:
        return query


class EkuivalensiCase(unittest.TestCase):
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

        seeder = SampleDatabaseSeeder()
        # Skema dibuat lewat katalog SM karena facade create_table masih stub.
        # Ganti dengan self.sm.create_table setelah SM menyediakannya.
        seeder._create_tables(self.sm._catalog)
        seeder.load_into(self.sm)

        self.qo = QueryOptimizerFacade(self.sm)
        self.qp_nyala = QueryProcessorFacade(self.qo, self.sm, self.ccm, self.frm)
        self.qp_mati = QueryProcessorFacade(
            QOTanpaOptimasi(self.sm), self.sm, self.ccm, self.frm
        )

    def jalankan(self, qp: QueryProcessorFacade, sql: str) -> ExecutionResult:
        session = qp.open_session()
        try:
            return qp.execute_query(sql, session)
        finally:
            qp.close_session(session.session_id)

    def assert_hasil_sama(
        self, sql: str, kolom_urut: int | None = None, boleh_kosong: bool = False
    ) -> ExecutionResult:
        nyala = self.jalankan(self.qp_nyala, sql)
        mati = self.jalankan(self.qp_mati, sql)

        # Cek ok dulu: dua hasil gagal sama-sama kosong, tapi tidak menguji apa pun.
        self.assertTrue(nyala.ok, f"dengan optimasi gagal: {nyala.message}")
        self.assertTrue(mati.ok, f"tanpa optimasi gagal: {mati.message}")
        if not boleh_kosong:
            self.assertGreater(len(mati.rows), 0, "query contoh seharusnya punya hasil")

        self.assertEqual(nyala.columns, mati.columns)
        self.assertEqual(
            Counter(tuple(row) for row in nyala.rows),
            Counter(tuple(row) for row in mati.rows),
        )
        if kolom_urut is not None:
            self.assertEqual(
                [row[kolom_urut] for row in nyala.rows],
                [row[kolom_urut] for row in mati.rows],
            )
        return nyala

    def render_plan(self, sql: str, optimasi: bool) -> str:
        parsed = self.qo.parse_query(sql)
        if optimasi:
            parsed = self.qo.optimize_query(parsed)
        return PlanPrinter().render(parsed.plan)


class TestPersiapan(EkuivalensiCase):
    def test_data_contoh_termuat(self) -> None:
        jumlah = {t: len(list(self.sm.scan(t))) for t in ("student", "course", "enroll")}
        self.assertEqual(jumlah, {"student": 60, "course": 50, "enroll": 200})

    def test_optimizer_benar_benar_mengubah_plan(self) -> None:
        # Jika plan tidak pernah berubah, uji ekuivalensi tidak menguji apa pun.
        sql = (
            "SELECT student.name, enroll.grade FROM student "
            "JOIN enroll ON student.id = enroll.student_id WHERE student.gpa > 3.5;"
        )
        self.assertNotEqual(self.render_plan(sql, False), self.render_plan(sql, True))


class TestSatuTabel(EkuivalensiCase):
    def test_seleksi_dan_proyeksi(self) -> None:
        queries = [
            "SELECT * FROM student;",
            "SELECT * FROM student WHERE gpa > 3.5;",
            "SELECT name FROM student WHERE department = 'CS' AND gpa >= 3.0;",
            "SELECT * FROM enroll WHERE grade + 1 > 4;",
            "SELECT name, credits FROM course WHERE credits <> 3;",
        ]
        for sql in queries:
            with self.subTest(sql=sql):
                self.assert_hasil_sama(sql)

    def test_duplikat_tetap_dihitung(self) -> None:
        # department hanya punya 3 nilai, jadi hasilnya penuh duplikat.
        hasil = self.assert_hasil_sama("SELECT department FROM student;")
        self.assertEqual(len(hasil.rows), 60)

    def test_hasil_kosong(self) -> None:
        hasil = self.assert_hasil_sama(
            "SELECT * FROM student WHERE gpa > 5;", boleh_kosong=True
        )
        self.assertEqual(hasil.rows, [])


class TestJoin(EkuivalensiCase):
    def test_join_dua_tabel(self) -> None:
        queries = [
            "SELECT student.name, enroll.grade FROM student "
            "JOIN enroll ON student.id = enroll.student_id WHERE student.gpa > 3.5;",
            "SELECT * FROM student, course WHERE student.id = course.id;",
            "SELECT * FROM student, course WHERE student.id < 3 AND course.id < 3;",
        ]
        for sql in queries:
            with self.subTest(sql=sql):
                self.assert_hasil_sama(sql)

    def test_join_tiga_tabel(self) -> None:
        queries = [
            "SELECT student.name, course.name, enroll.grade FROM student "
            "JOIN enroll ON student.id = enroll.student_id "
            "JOIN course ON enroll.course_id = course.id "
            "WHERE course.credits = 4 AND enroll.grade > 2.0;",
            "SELECT * FROM student, enroll, course "
            "WHERE student.id = enroll.student_id AND enroll.course_id = course.id "
            "AND student.department = 'MATH';",
        ]
        for sql in queries:
            with self.subTest(sql=sql):
                self.assert_hasil_sama(sql)


class TestOrderBy(EkuivalensiCase):
    def test_urutan_sama(self) -> None:
        self.assert_hasil_sama(
            "SELECT name, gpa FROM student WHERE department = 'PHYS' ORDER BY gpa DESC;",
            kolom_urut=1,
        )
        self.assert_hasil_sama(
            "SELECT student.name, enroll.grade FROM student "
            "JOIN enroll ON student.id = enroll.student_id ORDER BY enroll.grade;",
            kolom_urut=1,
        )


class TestIndex(EkuivalensiCase):
    def test_index_scan_hasilnya_sama(self) -> None:
        dibuat = self.jalankan(self.qp_nyala, "CREATE INDEX idx_sid ON student(id);")
        self.assertTrue(dibuat.ok, dibuat.message)

        sql = "SELECT * FROM student WHERE id = 7;"
        self.assertIn("IndexScan", self.render_plan(sql, True))
        hasil = self.assert_hasil_sama(sql)
        self.assertEqual(len(hasil.rows), 1)

        self.assert_hasil_sama(
            "SELECT student.name, enroll.grade FROM student "
            "JOIN enroll ON student.id = enroll.student_id WHERE student.id = 7;"
        )


if __name__ == "__main__":
    unittest.main()
