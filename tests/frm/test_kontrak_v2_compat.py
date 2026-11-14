"""Uji kompatibilitas kontrak v2 (5 November 2026) terhadap komponen FRM.

Fokus: field `LogRecord.prev_lsn`, `LogKind.RESTORE`, dan `LogWriteError`.
Test yang gagal menandakan perubahan kontrak di branch ini belum ditindaklanjuti
oleh implementasi `failure_recovery/*`.
"""

from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from common.errors import LogWriteError
from common.interfaces import FailureRecoveryManager as FailureRecoveryManagerBase
from common.log import LogKind, LogRecord, LogResult, RecoveryTarget
from failure_recovery import FailureRecoveryManager
from failure_recovery.log import codec
from failure_recovery.log.wal_logger import WalLogger


class KontrakV2TestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "wal.log"
        self.logger = WalLogger(str(self.path))
        self.addCleanup(self.logger.close)
        self.frm = FailureRecoveryManager(log_path=str(self.path))
        self.addCleanup(self.frm.close)


class TestPrevLsn(KontrakV2TestCase):
    def test_01_default_prev_lsn_minus_satu(self):
        """Field baru wajib punya default -1 agar pemakai lama tetap jalan."""
        self.assertEqual(LogRecord(LogKind.BEGIN, tx_id=1).prev_lsn, -1)
        self.assertEqual(LogRecord(LogKind.RESTORE).prev_lsn, -1)

    def test_02_append_mengisi_prev_lsn_transaksi_sama(self):
        """`write_log` FRM harus mengisi prev_lsn ke catatan sebelumnya transaksi itu."""
        begin = LogRecord(LogKind.BEGIN, tx_id=1)
        data = LogRecord(LogKind.DATA, tx_id=1)
        self.logger.append(begin)
        self.logger.append(data)
        self.assertEqual(begin.prev_lsn, -1)
        self.assertEqual(data.prev_lsn, begin.lsn)

    def test_03_prev_lsn_per_transaksi_saat_interleaved(self):
        """prev_lsn menunjuk catatan transaksi yang sama, bukan LSN global sebelumnya."""
        tx1_begin = LogRecord(LogKind.BEGIN, tx_id=1)
        tx2_begin = LogRecord(LogKind.BEGIN, tx_id=2)
        tx1_data = LogRecord(LogKind.DATA, tx_id=1)
        tx2_data = LogRecord(LogKind.DATA, tx_id=2)
        for record in (tx1_begin, tx2_begin, tx1_data, tx2_data):
            self.logger.append(record)
        self.assertEqual(tx1_data.prev_lsn, tx1_begin.lsn)
        self.assertEqual(tx2_data.prev_lsn, tx2_begin.lsn)

    def test_04_codec_menyimpan_prev_lsn(self):
        """prev_lsn harus ikut terserialisasi agar rollback bisa menelusuri log dari disk."""
        asli = LogRecord(LogKind.COMMIT, tx_id=1, lsn=9, timestamp=1.0, prev_lsn=7)
        hasil = codec.parse(codec.serialize(asli))
        self.assertEqual(hasil.prev_lsn, 7)

    def test_05_prev_lsn_lanjut_setelah_restart(self):
        """Peta prev_lsn yang dibangun ulang harus benar-benar dipakai saat append."""
        self.logger.log_begin(1)
        self.logger.log_commit(1)
        self.logger.close()

        baru = WalLogger(str(self.path))
        self.addCleanup(baru.close)
        record = LogRecord(LogKind.BEGIN, tx_id=1)
        baru.append(record)
        self.assertEqual(record.prev_lsn, 2)


class TestRestore(KontrakV2TestCase):
    def test_06_log_kind_restore_round_trip(self):
        """RESTORE baru harus bisa diserialisasi dan dibaca kembali."""
        record = LogRecord(LogKind.RESTORE, lsn=3, timestamp=1.0)
        self.assertEqual(codec.parse(codec.serialize(record)), record)

    def test_07_log_restore_menulis_record_restore(self):
        """Setelah LogKind.RESTORE ada, log_restore tidak boleh lagi menjadi stub."""
        from failure_recovery.log.log_reader import LogReader

        lsn = self.logger.log_restore(RecoveryTarget(tx_id=1))
        self.assertGreater(lsn, 0)
        records = LogReader(str(self.path)).read_log(-1)
        self.assertEqual([r.kind for r in records], [LogKind.RESTORE])


class TestLogWriteError(KontrakV2TestCase):
    def test_08_gagal_tulis_memakai_log_write_error(self):
        """Kegagalan I/O log sekarang punya tipe khusus LogWriteError."""

        class _GagalTulis:
            def write(self, data):
                raise OSError("disk penuh")

            def flush(self):
                return None

        asli = self.logger._file
        self.logger._file = _GagalTulis()
        try:
            with self.assertRaises(LogWriteError):
                self.logger.append(LogRecord(LogKind.BEGIN, tx_id=1))
        finally:
            self.logger._file = asli

    def test_09_facade_masih_menangani_log_write_error(self):
        """Facade lama menangkap LogError; LogWriteError turunannya harus tetap tertangkap."""
        with patch.object(
            WalLogger,
            "append",
            side_effect=LogWriteError("gagal tulis"),
        ):
            hasil = self.frm.write_log(LogRecord(LogKind.BEGIN, tx_id=1))
        self.assertIsInstance(hasil, LogResult)
        self.assertFalse(hasil.ok)
        self.assertEqual(hasil.message, "gagal tulis")


class TestKontrakFacade(KontrakV2TestCase):
    def test_10_facade_tetap_memenuhi_abc_frm(self):
        """Perubahan kontrak v2 tidak boleh mengubah signature ABC FRM."""
        self.assertTrue(issubclass(FailureRecoveryManager, FailureRecoveryManagerBase))
        self.assertFalse(inspect.isabstract(FailureRecoveryManager))
        for name in FailureRecoveryManagerBase.__abstractmethods__:
            with self.subTest(method=name):
                expected = list(
                    inspect.signature(getattr(FailureRecoveryManagerBase, name)).parameters
                )
                actual = list(inspect.signature(getattr(FailureRecoveryManager, name)).parameters)
                self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
