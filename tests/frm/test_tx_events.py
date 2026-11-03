"""Uji pencatatan peristiwa transaksi ke WAL (FR-02)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from common.log import LogKind, LogRecord
from common.storage import RID
from common.transaction import ChangeOp, DataChange
from failure_recovery import FailureRecoveryManager


class TxEventTestCase(unittest.TestCase):
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

    def _kinds(self, tx_id: int | None = None) -> list[LogKind]:
        return [record.kind for record in self.frm.read_log(tx_id)]


class TestAlurSatuTransaksi(TxEventTestCase):
    def test_urutan_begin_data_commit(self):
        self.frm.write_log(LogRecord(LogKind.BEGIN, tx_id=1))
        self.frm.write_log(self._data_record(1))
        self.frm.write_log(LogRecord(LogKind.COMMIT, tx_id=1))
        self.assertEqual(
            self._kinds(1),
            [LogKind.BEGIN, LogKind.DATA, LogKind.COMMIT],
        )
        self.assertEqual([r.lsn for r in self.frm.read_log(1)], [1, 2, 3])

    def test_lsn_event_berurutan_dari_satu(self):
        self.assertEqual(self.frm.write_log(LogRecord(LogKind.BEGIN, tx_id=1)).lsn, 1)
        self.assertEqual(self.frm.write_log(LogRecord(LogKind.COMMIT, tx_id=1)).lsn, 2)
        self.assertEqual(self.frm.write_log(LogRecord(LogKind.BEGIN, tx_id=2)).lsn, 3)


class TestAlurAbort(TxEventTestCase):
    def test_urutan_begin_abort_request_abort(self):
        self.frm.write_log(LogRecord(LogKind.BEGIN, tx_id=7))
        self.frm.write_log(LogRecord(LogKind.ABORT_REQUEST, tx_id=7))
        self.frm.write_log(LogRecord(LogKind.ABORT, tx_id=7))
        self.assertEqual(
            self._kinds(7),
            [LogKind.BEGIN, LogKind.ABORT_REQUEST, LogKind.ABORT],
        )
        self.assertEqual([r.lsn for r in self.frm.read_log(7)], [1, 2, 3])


class TestPolaFlushCommit(TxEventTestCase):
    def test_write_commit_tidak_auto_flush(self):
        # Keputusan: flush COMMIT/ABORT urusan CCM, bukan FRM.
        with patch("failure_recovery.log.wal_logger.os.fsync") as fsync:
            result = self.frm.write_log(LogRecord(LogKind.COMMIT, tx_id=1))
            fsync.assert_not_called()
        self.assertTrue(result.ok)

    def test_flush_setelah_commit_memanggil_fsync(self):
        result = self.frm.write_log(LogRecord(LogKind.COMMIT, tx_id=1))
        with patch("failure_recovery.log.wal_logger.os.fsync") as fsync:
            self.assertTrue(self.frm.flush_log(result.lsn))
            fsync.assert_called_once()

    def test_flush_ulang_commit_tidak_fsync_lagi(self):
        result = self.frm.write_log(LogRecord(LogKind.COMMIT, tx_id=1))
        self.frm.flush_log(result.lsn)
        with patch("failure_recovery.log.wal_logger.os.fsync") as fsync:
            self.assertTrue(self.frm.flush_log(result.lsn))
            fsync.assert_not_called()


class TestTransaksiBersisikan(TxEventTestCase):
    def test_dua_transaksi_interleaved_terpisah(self):
        self.frm.write_log(LogRecord(LogKind.BEGIN, tx_id=1))
        self.frm.write_log(LogRecord(LogKind.BEGIN, tx_id=2))
        self.frm.write_log(self._data_record(1))
        self.frm.write_log(LogRecord(LogKind.COMMIT, tx_id=1))
        self.frm.write_log(self._data_record(2, table="course"))
        self.frm.write_log(LogRecord(LogKind.COMMIT, tx_id=2))

        self.assertEqual(
            self._kinds(1),
            [LogKind.BEGIN, LogKind.DATA, LogKind.COMMIT],
        )
        self.assertEqual(
            self._kinds(2),
            [LogKind.BEGIN, LogKind.DATA, LogKind.COMMIT],
        )
        self.assertEqual([r.lsn for r in self.frm.read_log(1)], [1, 3, 4])
        self.assertEqual([r.lsn for r in self.frm.read_log(2)], [2, 5, 6])

    def test_backward_scan_transaksi_menurun(self):
        self.frm.write_log(LogRecord(LogKind.BEGIN, tx_id=1))
        self.frm.write_log(LogRecord(LogKind.BEGIN, tx_id=2))
        self.frm.write_log(LogRecord(LogKind.COMMIT, tx_id=1))
        self.frm.write_log(LogRecord(LogKind.ABORT_REQUEST, tx_id=2))
        self.frm.write_log(LogRecord(LogKind.ABORT, tx_id=2))
        self.assertEqual([r.lsn for r in self.frm.read_log(2)], [2, 4, 5])
        self.assertEqual([r.lsn for r in self.frm._log_reader.backward_scan(2)], [5, 4, 2])
        self.assertEqual([r.lsn for r in self.frm._log_reader.backward_scan(1)], [3, 1])


class TestPersistensiEvent(TxEventTestCase):
    def test_event_bertahan_setelah_restart(self):
        self.frm.write_log(LogRecord(LogKind.BEGIN, tx_id=1))
        self.frm.write_log(self._data_record(1))
        self.frm.write_log(LogRecord(LogKind.COMMIT, tx_id=1))
        self.frm.close()

        baru = FailureRecoveryManager(log_path=str(self.path))
        self.addCleanup(baru.close)
        self.assertEqual(
            [r.kind for r in baru.read_log(1)],
            [LogKind.BEGIN, LogKind.DATA, LogKind.COMMIT],
        )
        self.assertEqual(baru.write_log(LogRecord(LogKind.BEGIN, tx_id=2)).lsn, 4)


class TestValidasiStruktural(TxEventTestCase):
    def test_event_bukan_record_ditolak(self):
        result = self.frm.write_log("bukan record")
        self.assertFalse(result.ok)
        self.assertIsNone(result.lsn)
        self.assertTrue(result.message)

    def test_event_tanpa_tx_id_tetap_ditulis(self):
        # Kontrak tidak mewajibkan tx_id; validasi semantik bukan keputusan FR-02.
        result = self.frm.write_log(LogRecord(LogKind.BEGIN))
        self.assertTrue(result.ok)
        self.assertEqual(self._kinds(), [LogKind.BEGIN])

    def test_checkpoint_tanpa_tx_id_valid(self):
        self.assertTrue(self.frm.write_log(LogRecord(LogKind.CHECKPOINT_BEGIN)).ok)
        self.assertEqual(self._kinds(), [LogKind.CHECKPOINT_BEGIN])
