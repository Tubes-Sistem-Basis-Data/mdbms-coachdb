"""Uji facade FailureRecoveryManager untuk FR-01 (write_log, flush_log, read_log)."""

from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path

from common.interfaces import FailureRecoveryManager as FailureRecoveryManagerBase
from common.log import LogKind, LogRecord, LogResult
from common.storage import RID
from common.transaction import NO_RECORD, ChangeOp, DataChange, TxStatus
from failure_recovery import FailureRecoveryManager
from failure_recovery.log import codec


class FacadeWalTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "wal.log"
        self.frm = FailureRecoveryManager(log_path=str(self.path))
        self.addCleanup(self.frm.close)

    def _data_record(self, tx_id: int, table: str = "student") -> LogRecord:
        return LogRecord(
            kind=LogKind.DATA,
            tx_id=tx_id,
            change=DataChange(
                table=table,
                rid=RID(table, 0, 0),
                op=ChangeOp.UPDATE,
                before=[1],
                after=[2],
            ),
        )


class TestKontrak(FacadeWalTestCase):
    def test_subclass_kontrak(self):
        self.assertTrue(issubclass(FailureRecoveryManager, FailureRecoveryManagerBase))
        self.assertFalse(inspect.isabstract(FailureRecoveryManager))

    def test_signature_sama_dengan_kontrak(self):
        for name in FailureRecoveryManagerBase.__abstractmethods__:
            with self.subTest(method=name):
                expected = list(inspect.signature(getattr(FailureRecoveryManagerBase, name)).parameters)
                actual = list(inspect.signature(getattr(FailureRecoveryManager, name)).parameters)
                self.assertEqual(actual, expected)


class TestWriteLog(FacadeWalTestCase):
    def test_write_log_mengisi_lsn_dan_timestamp(self):
        record = LogRecord(LogKind.BEGIN, tx_id=1)
        result = self.frm.write_log(record)
        self.assertIsInstance(result, LogResult)
        self.assertTrue(result.ok)
        self.assertEqual(result.lsn, 1)
        self.assertEqual(record.lsn, 1)
        self.assertGreater(record.timestamp, 0.0)

    def test_write_log_lsn_berurutan(self):
        self.assertEqual(self.frm.write_log(LogRecord(LogKind.BEGIN, tx_id=1)).lsn, 1)
        self.assertEqual(self.frm.write_log(LogRecord(LogKind.COMMIT, tx_id=1)).lsn, 2)

    def test_write_log_record_tidak_valid(self):
        result = self.frm.write_log("bukan record")
        self.assertFalse(result.ok)
        self.assertIsNone(result.lsn)
        self.assertTrue(result.message)

    def test_write_log_tersimpan_ke_file(self):
        self.frm.write_log(self._data_record(4))
        isi = self.path.read_text()
        self.assertEqual(isi.count("\n"), 1)
        self.assertIn('"kind":"DATA"', isi)


class TestFlushLog(FacadeWalTestCase):
    def test_flush_log_nol_kembali_true(self):
        self.assertTrue(self.frm.flush_log(0))

    def test_flush_log_lsn_tercatat_kembali_true(self):
        self.frm.write_log(LogRecord(LogKind.BEGIN, tx_id=1))
        self.assertTrue(self.frm.flush_log(1))

    def test_flush_log_lsn_besar_kembali_true(self):
        self.frm.write_log(LogRecord(LogKind.BEGIN, tx_id=1))
        self.assertTrue(self.frm.flush_log(999))

    def test_flush_log_setelah_close_kembali_false(self):
        self.frm.write_log(LogRecord(LogKind.BEGIN, tx_id=1))
        self.frm.close()
        self.assertFalse(self.frm.flush_log(1))


class TestReadLog(FacadeWalTestCase):
    def test_read_log_kosong(self):
        self.assertEqual(self.frm.read_log(), [])

    def test_read_log_filter_transaksi(self):
        self.frm.write_log(LogRecord(LogKind.BEGIN, tx_id=1))
        self.frm.write_log(self._data_record(1))
        self.frm.write_log(LogRecord(LogKind.BEGIN, tx_id=2))
        self.assertEqual(len(self.frm.read_log()), 3)
        self.assertEqual(len(self.frm.read_log(1)), 2)
        self.assertEqual(len(self.frm.read_log(2)), 1)
        self.assertEqual(self.frm.read_log(99), [])

    def test_read_log_mengikuti_path(self):
        self.frm.write_log(LogRecord(LogKind.BEGIN, tx_id=8))
        self.assertEqual(codec.get_log_path(), self.path)


class TestDependensi(FacadeWalTestCase):
    def test_set_dependencies_menyimpan_sm_dan_ccm(self):
        self.frm.set_dependencies(sm="sm", ccm="ccm")
        self.assertEqual(self.frm._sm, "sm")
        self.assertEqual(self.frm._ccm, "ccm")

    def test_close_idempoten(self):
        self.frm.close()
        self.frm.close()


class _FakeStorageManager:
    """SM minimal untuk memverifikasi wiring rollback di facade."""

    def __init__(self) -> None:
        self.rows: dict[RID, list] = {}

    def insert(self, table: str, rid: RID, values: list, lsn: int | None) -> None:
        self.rows[rid] = list(values)

    def update(self, table: str, rid: RID, values: list, lsn: int | None) -> None:
        self.rows[rid] = list(values)

    def delete(self, table: str, rid: RID, lsn: int | None) -> None:
        self.rows.pop(rid, None)


class _GagalStorageManager(_FakeStorageManager):
    def delete(self, table: str, rid: RID, lsn: int | None) -> None:
        from common.errors import StorageError

        raise StorageError("gagal hapus")


class TestRollbackFacade(FacadeWalTestCase):
    def catat(
        self,
        tx_id: int,
        op: ChangeOp,
        rid: RID,
        before,
        after,
    ) -> None:
        hasil = self.frm.write_log(
            LogRecord(
                kind=LogKind.DATA,
                tx_id=tx_id,
                change=DataChange(
                    table=rid.table,
                    rid=rid,
                    op=op,
                    before=before,
                    after=after,
                ),
            )
        )
        self.assertTrue(hasil.ok)

    def test_rollback_tanpa_dependensi_mengembalikan_failed(self) -> None:
        hasil = self.frm.rollback(1)
        self.assertIs(hasil.status, TxStatus.FAILED)
        self.assertFalse(hasil.ok)
        self.assertTrue(hasil.message)

    def test_rollback_mendelegasikan_ke_rollback_manager(self) -> None:
        sm = _FakeStorageManager()
        rid = RID("student", 0, 0)
        self.frm.write_log(LogRecord(LogKind.BEGIN, tx_id=1))
        self.catat(1, ChangeOp.INSERT, rid, NO_RECORD, [1, "A"])
        sm.rows[rid] = [1, "A"]
        self.frm.set_dependencies(sm, ccm=None)

        hasil = self.frm.rollback(1)

        self.assertIs(hasil.status, TxStatus.ABORTED)
        self.assertTrue(hasil.ok)
        self.assertNotIn(rid, sm.rows)
        self.assertEqual(
            [record.kind for record in self.frm.read_log(1)],
            [LogKind.BEGIN, LogKind.DATA, LogKind.ABORT_REQUEST, LogKind.ABORT],
        )

    def test_rollback_transaksi_committed_tidak_ok(self) -> None:
        self.frm.write_log(LogRecord(LogKind.BEGIN, tx_id=1))
        self.frm.write_log(LogRecord(LogKind.COMMIT, tx_id=1))
        self.frm.set_dependencies(_FakeStorageManager(), ccm=None)

        hasil = self.frm.rollback(1)

        self.assertIs(hasil.status, TxStatus.COMMITTED)
        self.assertFalse(hasil.ok)

    def test_rollback_transaksi_tidak_dikenal_gagal(self) -> None:
        self.frm.set_dependencies(_FakeStorageManager(), ccm=None)

        hasil = self.frm.rollback(99)

        self.assertIs(hasil.status, TxStatus.FAILED)
        self.assertFalse(hasil.ok)

    def test_rollback_transaksi_sudah_abort_ok(self) -> None:
        self.frm.write_log(LogRecord(LogKind.BEGIN, tx_id=1))
        self.frm.write_log(LogRecord(LogKind.ABORT, tx_id=1))
        self.frm.set_dependencies(_FakeStorageManager(), ccm=None)

        hasil = self.frm.rollback(1)

        self.assertIs(hasil.status, TxStatus.ABORTED)
        self.assertTrue(hasil.ok)

    def test_rollback_error_sm_mengembalikan_failed(self) -> None:
        rid = RID("student", 0, 0)
        self.frm.write_log(LogRecord(LogKind.BEGIN, tx_id=1))
        self.catat(1, ChangeOp.INSERT, rid, NO_RECORD, [1])
        self.frm.set_dependencies(_GagalStorageManager(), ccm=None)

        hasil = self.frm.rollback(1)

        self.assertIs(hasil.status, TxStatus.FAILED)
        self.assertFalse(hasil.ok)
        self.assertIn("gagal hapus", hasil.message)


if __name__ == "__main__":
    unittest.main()
