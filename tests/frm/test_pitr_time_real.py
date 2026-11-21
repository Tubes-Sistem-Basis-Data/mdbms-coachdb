"""Uji PITR berbasis waktu (FR-05) dengan SM, FRM, dan CCM asli.

Dua jalur diuji di protokol lock dan timestamp:
- level FRM: transaksi dijalankan lewat CCM asli, restore lewat `frm.recover_to`;
- jalur demo QP: runtime dari `main.bootstrap`, restore lewat `RESTORE TO TIME`.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import main
from common.catalog import ColumnSchema, DataType
from common.log import LogKind, LogRecord, RecoveryTarget
from common.transaction import (
    NO_RECORD,
    AccessStatus,
    Action,
    ChangeOp,
    DataChange,
    ResourceId,
)
from concurrency_control import CCManager
from failure_recovery import FailureRecoveryManager
from failure_recovery.checkpoint.checkpoint_store import CheckpointStore
from failure_recovery.log import codec
from storage_manager.facade import StorageManager

PROTOCOLS = ("lock", "timestamp")

AWAL = [[1, "andi", 3.0], [2, "budi", 3.5]]
SETELAH_T1 = AWAL + [[3, "citra", 3.2]]


def _buat_tabel_student(sm: StorageManager) -> None:
    # TODO: ganti ke sm.create_table(...) setelah facade SM tidak lagi stub.
    sm._catalog.create_table(
        "student",
        (
            ColumnSchema("id", DataType.INTEGER, nullable=False),
            ColumnSchema("name", DataType.VARCHAR, length=50),
            ColumnSchema("gpa", DataType.FLOAT),
        ),
        ("id",),
        (),
    )
    sm.bulk_load("student", [list(row) for row in AWAL])


def _isi_tabel(sm: StorageManager) -> list[list]:
    return sorted(list(row.values) for row in sm.scan("student"))


def _tunggu_melewati(batas: float) -> None:
    """Resolusi `time.time()` di Windows sekitar 15 ms; T2 harus mulai setelah batas."""
    while time.time() <= batas + 0.002:
        time.sleep(0.001)


def _batas_waktu_tx(frm: FailureRecoveryManager, tx_id: int) -> float:
    """Timestamp catatan WAL terakhir milik `tx_id` (COMMIT-nya)."""
    records = frm.read_log(tx_id)
    if not records or records[-1].kind is not LogKind.COMMIT:
        raise AssertionError(f"T{tx_id} belum punya COMMIT di WAL")
    return records[-1].timestamp


def _waktu_batas(timestamp: float) -> datetime:
    """Waktu lokal bermikrodetik yang tidak lebih kecil dari `timestamp`."""
    # PITR mengubah target lewat datetime (presisi mikrodetik); tanpa pembulatan
    # ke atas, COMMIT tepat di batas bisa ikut dibatalkan.
    waktu = datetime.fromtimestamp(timestamp)
    if waktu.timestamp() < timestamp:
        waktu += timedelta(microseconds=1)
    return waktu


def _kinds(records: list[LogRecord]) -> list[LogKind]:
    return [record.kind for record in records]


class _PulihkanLogPath(unittest.TestCase):
    """Kembalikan path log global codec yang diubah WalLogger."""

    def setUp(self) -> None:
        self.addCleanup(codec.set_log_path, codec.get_log_path())

    def _pakai_data_dir(self, folder: str) -> None:
        self.data_dir = Path(folder)
        self.log_path = self.data_dir / "wal.log"

    def _store(self) -> CheckpointStore:
        return CheckpointStore(
            str(self.log_path.with_suffix(".checkpoint.json")),
            str(self.data_dir / "snapshots"),
        )

    def _cek_restore_dan_checkpoint(self, records: list[LogRecord]) -> None:
        kinds = _kinds(records)
        self.assertIn(LogKind.RESTORE, kinds)
        restore = max(r.lsn for r in records if r.kind is LogKind.RESTORE)
        sesudah = [r.kind for r in records if r.lsn > restore]
        self.assertIn(LogKind.CHECKPOINT_BEGIN, sesudah)
        self.assertIn(LogKind.CHECKPOINT_END, sesudah)

        meta = self._store().find_latest_completed()
        self.assertIsNotNone(meta)
        self.assertTrue(meta.completed)
        self.assertGreater(meta.checkpoint_lsn, restore)


class PITRTimeRealFRMTest(_PulihkanLogPath):
    """Restore ke waktu lewat facade FRM dengan SM dan CCM asli."""

    def _runtime(self, protocol: str, stack: contextlib.ExitStack) -> None:
        self.sm = StorageManager(str(self.data_dir))
        stack.callback(self.sm.close)
        _buat_tabel_student(self.sm)
        self.frm = FailureRecoveryManager(log_path=str(self.log_path))
        stack.callback(self.frm.close)
        self.ccm = CCManager(self.frm)
        self.frm.set_dependencies(self.sm, self.ccm)
        self.sm.set_log_flusher(self.frm.flush_log)
        self.ccm.set_protocol(main.PROTOCOLS[protocol])
        self.ccm.initialize(0)

    def _izinkan(self, tx_id: int, change: DataChange) -> int:
        response = self.ccm.validate_object(
            tx_id, ResourceId(change.table, change.rid), Action.WRITE, change
        )
        self.assertIs(response.status, AccessStatus.ALLOWED, response.message)
        self.assertIsNotNone(response.lsn)
        return response.lsn

    def _insert(self, tx_id: int, values: list) -> None:
        values = self.sm.validate("student", list(values))
        rid = self.sm.allocate_rid("student")
        change = DataChange("student", rid, ChangeOp.INSERT, NO_RECORD, list(values))
        self.sm.insert("student", rid, list(values), self._izinkan(tx_id, change))

    def _update_gpa(self, tx_id: int, student_id: int, gpa: float) -> None:
        row = next(r for r in self.sm.scan("student") if r.values[0] == student_id)
        after = list(row.values)
        after[2] = gpa
        after = self.sm.validate("student", after, rid=row.rid)
        change = DataChange("student", row.rid, ChangeOp.UPDATE, list(row.values), after)
        self.sm.update("student", row.rid, after, self._izinkan(tx_id, change))

    def _commit(self, tx_id: int) -> None:
        result = self.ccm.commit(tx_id)
        self.assertTrue(result.ok, result.message)

    def _skenario_t1_t2(self) -> float:
        """T1 insert+commit, lalu T2 update+commit. Kembalikan batas waktu T1."""
        t1 = self.ccm.begin_transaction()
        self._insert(t1, SETELAH_T1[-1])
        self._commit(t1)
        batas = _batas_waktu_tx(self.frm, t1)
        self.assertEqual(_isi_tabel(self.sm), SETELAH_T1)

        _tunggu_melewati(batas)
        t2 = self.ccm.begin_transaction()
        self._update_gpa(t2, 1, 4.0)
        self._update_gpa(t2, 3, 2.5)
        self._commit(t2)
        self.assertNotEqual(_isi_tabel(self.sm), SETELAH_T1)
        return _waktu_batas(batas).timestamp()

    def test_restore_ke_waktu_commit_t1(self) -> None:
        for protocol in PROTOCOLS:
            with self.subTest(protocol=protocol):
                self._jalankan_terisolasi(protocol, self._cek_restore_sekali)

    def test_restore_berulang_ke_waktu_sama(self) -> None:
        for protocol in PROTOCOLS:
            with self.subTest(protocol=protocol):
                self._jalankan_terisolasi(protocol, self._cek_restore_berulang)

    def test_transaksi_baru_setelah_restore(self) -> None:
        for protocol in PROTOCOLS:
            with self.subTest(protocol=protocol):
                self._jalankan_terisolasi(protocol, self._cek_lanjut_setelah_restore)

    def _jalankan_terisolasi(self, protocol: str, cek) -> None:
        with TemporaryDirectory() as folder:
            self._pakai_data_dir(folder)
            # FRM dan SM ditutup sebelum folder dihapus; Windows menolak hapus berkas terbuka.
            with contextlib.ExitStack() as stack:
                self._runtime(protocol, stack)
                cek()

    def _cek_restore_sekali(self) -> None:
        batas = self._skenario_t1_t2()

        result = self.frm.recover_to(RecoveryTarget(timestamp=batas))

        self.assertTrue(result.ok, result.message)
        self.assertEqual(_isi_tabel(self.sm), SETELAH_T1)
        self._cek_restore_dan_checkpoint(self.frm.read_log())

    def _cek_restore_berulang(self) -> None:
        batas = self._skenario_t1_t2()

        pertama = self.frm.recover_to(RecoveryTarget(timestamp=batas))
        kedua = self.frm.recover_to(RecoveryTarget(timestamp=batas))

        self.assertTrue(pertama.ok, pertama.message)
        self.assertTrue(kedua.ok, kedua.message)
        self.assertEqual(_isi_tabel(self.sm), SETELAH_T1)
        records = self.frm.read_log()
        self.assertEqual(_kinds(records).count(LogKind.RESTORE), 2)
        self._cek_restore_dan_checkpoint(records)

    def _cek_lanjut_setelah_restore(self) -> None:
        batas = self._skenario_t1_t2()
        result = self.frm.recover_to(RecoveryTarget(timestamp=batas))
        self.assertTrue(result.ok, result.message)

        # main/QP memanggil ccm.initialize(last_tx_id) setelah restore
        self.ccm.initialize(result.last_tx_id)
        t3 = self.ccm.begin_transaction()
        self.assertGreater(t3, result.last_tx_id)
        self._insert(t3, [4, "dewi", 3.9])
        self._commit(t3)
        self.assertEqual(_isi_tabel(self.sm), SETELAH_T1 + [[4, "dewi", 3.9]])


class PITRTimeRealQPTest(_PulihkanLogPath):
    """Restore ke waktu lewat perintah RESTORE TO TIME di QP (jalur demo)."""

    def _siapkan_data_dir(self) -> None:
        sm = StorageManager(str(self.data_dir))
        try:
            _buat_tabel_student(sm)
        finally:
            sm.close()

    def _bootstrap(self, protocol: str):
        args = argparse.Namespace(protocol=protocol, data_dir=str(self.data_dir))
        with contextlib.redirect_stdout(io.StringIO()):
            qp = main.bootstrap(args)
        session = qp.open_session()

        def tutup() -> None:
            qp.close_session(session.session_id)
            main.close_runtime(qp)

        return qp, session, tutup

    def _sql(self, qp, session, sql: str):
        result = qp.execute_query(sql, session)
        self.assertTrue(result.ok, f"{sql} -> {result.message}")
        return result

    def _select(self, qp, session) -> list[list]:
        return sorted(self._sql(qp, session, "SELECT * FROM student;").rows)

    def test_restore_to_time_lewat_qp(self) -> None:
        for protocol in PROTOCOLS:
            with self.subTest(protocol=protocol):
                with TemporaryDirectory() as folder:
                    self._pakai_data_dir(folder)
                    self._skenario_qp(protocol)

    def _skenario_qp(self, protocol: str) -> None:
        self._siapkan_data_dir()
        qp, session, tutup = self._bootstrap(protocol)
        try:
            self._sql(qp, session, "INSERT INTO student VALUES (3, 'citra', 3.2);")
            frm = qp._frm
            t1 = max(r.tx_id for r in frm.read_log() if r.kind is LogKind.COMMIT)
            batas = _batas_waktu_tx(frm, t1)
            self.assertEqual(self._select(qp, session), SETELAH_T1)

            _tunggu_melewati(batas)
            self._sql(qp, session, "UPDATE student SET gpa = 4.0 WHERE id = 1;")
            self._sql(qp, session, "DELETE FROM student WHERE id = 2;")
            self.assertNotEqual(self._select(qp, session), SETELAH_T1)

            self._sql(qp, session, "CHECKPOINT;")
            perintah = f"RESTORE TO TIME '{_waktu_batas(batas).isoformat(sep=' ')}';"
            self._sql(qp, session, perintah)
            self.assertEqual(self._select(qp, session), SETELAH_T1)

            # restore kedua ke waktu yang sama tidak mengubah hasil
            self._sql(qp, session, perintah)
            self.assertEqual(self._select(qp, session), SETELAH_T1)
            self._cek_restore_dan_checkpoint(frm.read_log())

            self._sql(qp, session, "INSERT INTO student VALUES (4, 'dewi', 3.9);")
            self.assertEqual(
                self._select(qp, session), SETELAH_T1 + [[4, "dewi", 3.9]]
            )
        finally:
            tutup()

        # hasil restore bertahan setelah restart normal
        qp, session, tutup = self._bootstrap(protocol)
        try:
            self.assertEqual(
                self._select(qp, session), SETELAH_T1 + [[4, "dewi", 3.9]]
            )
        finally:
            tutup()


if __name__ == "__main__":
    unittest.main()
