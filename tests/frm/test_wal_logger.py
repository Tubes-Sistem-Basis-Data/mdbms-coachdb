"""Uji penulis Write-Ahead Log (FR-01)."""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from common.errors import LogError
from common.log import LogKind, LogRecord, RecoveryTarget
from common.storage import RID
from common.transaction import ChangeOp, NO_RECORD
from failure_recovery.log import codec
from failure_recovery.log.log_reader import LogReader
from failure_recovery.log.wal_logger import WalLogger


class WalLoggerTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "wal.log"
        self.logger = WalLogger(str(self.path))
        self.addCleanup(self.logger.close)
        self.reader = LogReader(str(self.path))


class TestAppend(WalLoggerTestCase):
    def test_lsn_berurutan_dari_satu(self):
        self.assertEqual(self.logger.log_begin(1), 1)
        self.assertEqual(self.logger.log_commit(1), 2)
        self.assertEqual(self.logger.log_begin(2), 3)

    def test_record_tertulis_ke_file(self):
        self.logger.log_begin(4)
        records = self.reader.read_log(-1)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].kind, LogKind.BEGIN)
        self.assertEqual(records[0].lsn, 1)

    def test_log_write_membangun_datachange(self):
        rid = RID("student", 0, 5)
        self.logger.log_write(9, ChangeOp.UPDATE, rid, [1], [2])
        change = self.reader.read_log(-1)[0].change
        self.assertEqual(change.table, "student")
        self.assertEqual(change.op, ChangeOp.UPDATE)
        self.assertEqual(change.before, [1])
        self.assertEqual(change.after, [2])

    def test_log_write_sebelum_after_no_record(self):
        rid = RID("student", 1, 0)
        self.logger.log_write(2, ChangeOp.INSERT, rid, NO_RECORD, [7])
        change = self.reader.read_log(-1)[0].change
        self.assertIs(change.before, NO_RECORD)
        self.assertEqual(change.after, [7])

    def test_append_mengisi_lsn_dan_timestamp(self):
        record = LogRecord(LogKind.ABORT_REQUEST, tx_id=3)
        lsn = self.logger.append(record)
        self.assertEqual(record.lsn, lsn)
        self.assertGreater(record.timestamp, 0.0)

    def test_record_tidak_valid_melempar_log_error(self):
        with self.assertRaises(LogError):
            self.logger.append("bukan record")
        self.assertEqual(self.logger.next_lsn, 1)


class TestStateSaatRestart(WalLoggerTestCase):
    def test_lsn_lanjut_bukan_nol(self):
        self.logger.log_begin(1)
        self.logger.log_commit(1)
        self.logger.close()

        baru = WalLogger(str(self.path))
        self.addCleanup(baru.close)
        self.assertEqual(baru.log_begin(2), 3)

    def test_prev_lsn_dibangun_ulang(self):
        self.logger.log_begin(1)
        self.logger.log_write(1, ChangeOp.UPDATE, RID("t", 0, 0), [1], [2])
        self.logger.log_begin(2)
        self.logger.close()

        baru = WalLogger(str(self.path))
        self.addCleanup(baru.close)
        self.assertEqual(baru._prev_lsn(1), 2)
        self.assertEqual(baru._prev_lsn(2), 3)
        self.assertEqual(baru._prev_lsn(99), 0)

    def test_file_kosong_mulai_dari_satu(self):
        self.logger.close()
        baru = WalLogger(str(self.path))
        self.addCleanup(baru.close)
        self.assertEqual(baru.log_begin(1), 1)


class TestBarisRusak(WalLoggerTestCase):
    def _tulis_dulu_dan_tutup(self):
        self.logger.log_begin(1)
        self.logger.log_commit(1)
        self.logger.close()

    def test_baris_terakhir_terpotong_dipotong(self):
        self._tulis_dulu_dan_tutup()
        with open(self.path, "ab") as handle:
            handle.write(b'{"kind":"DATA","lsn":3,"timesta')

        baru = WalLogger(str(self.path))
        self.addCleanup(baru.close)
        isi = self.path.read_bytes()
        self.assertFalse(isi.splitlines()[-1].startswith(b'{"kind":"DATA","lsn":3'))
        self.assertEqual(baru.next_lsn, 3)
        self.assertEqual(baru.log_begin(2), 3)

    def test_append_setelah_potongan_menghasilkan_baris_valid(self):
        self._tulis_dulu_dan_tutup()
        with open(self.path, "ab") as handle:
            handle.write(b'{"kind":"DATA","lsn":3,"timesta')
        baru = WalLogger(str(self.path))
        self.addCleanup(baru.close)
        baru.log_commit(2)
        records = self.reader.read_log(-1)
        self.assertEqual([r.lsn for r in records], [1, 2, 3])

    def test_baris_rusak_di_tengah_melempar_log_error(self):
        self._tulis_dulu_dan_tutup()
        with open(self.path, "ab") as handle:
            handle.write(b"bukan-json\n")
            handle.write(codec.serialize(LogRecord(LogKind.BEGIN, tx_id=5, lsn=9)))
        with self.assertRaises(LogError):
            WalLogger(str(self.path))

    def test_file_tidak_ada_tidak_error(self):
        self.logger.close()
        self.path.unlink()
        baru = WalLogger(str(self.path))
        self.addCleanup(baru.close)
        self.assertEqual(baru.next_lsn, 1)


class TestFlush(WalLoggerTestCase):
    def test_flush_log_until_memanggil_fsync(self):
        self.logger.log_begin(1)
        with patch("failure_recovery.log.wal_logger.os.fsync") as fsync:
            self.logger.flush_log_until(1)
            fsync.assert_called_once()

    def test_flush_idempoten_per_lsn(self):
        self.logger.log_begin(1)
        with patch("failure_recovery.log.wal_logger.os.fsync") as fsync:
            self.logger.flush_log_until(1)
            self.logger.flush_log_until(1)
            self.logger.flush_log_until(0)
            fsync.assert_called_once()

    def test_flush_lsn_besar_flush_juga(self):
        self.logger.log_begin(1)
        with patch("failure_recovery.log.wal_logger.os.fsync") as fsync:
            self.logger.flush_log_until(50)
            fsync.assert_called_once()

    def test_gagal_fsync_melempar_log_error(self):
        self.logger.log_begin(1)
        with patch("failure_recovery.log.wal_logger.os.fsync", side_effect=OSError("disk penuh")):
            with self.assertRaises(LogError):
                self.logger.flush_log_until(1)

    def test_data_terlihat_reader_tanpa_fsync(self):
        self.logger.log_begin(1)
        self.assertEqual(len(self.reader.read_log(-1)), 1)


class TestLogLain(WalLoggerTestCase):
    def test_event_transaksi_tercatat(self):
        self.logger.log_begin(1)
        self.logger.log_commit(1)
        self.logger.log_abort_request(2)
        self.logger.log_abort_end(2)
        kinds = [r.kind for r in self.reader.read_log(-1)]
        self.assertEqual(
            kinds,
            [LogKind.BEGIN, LogKind.COMMIT, LogKind.ABORT_REQUEST, LogKind.ABORT],
        )

    def test_checkpoint_tercatat(self):
        self.logger.log_checkpoint_begin()
        self.logger.log_checkpoint_end()
        kinds = [r.kind for r in self.reader.read_log(-1)]
        self.assertEqual(kinds, [LogKind.CHECKPOINT_BEGIN, LogKind.CHECKPOINT_END])

    def test_clr_dan_restore_stub(self):
        rid = RID("t", 0, 0)
        self.assertEqual(self.logger.log_clr(1, rid, [1], [2]), 0)
        self.assertEqual(self.logger.log_restore(RecoveryTarget(tx_id=1)), 0)
        self.assertEqual(self.reader.read_log(-1), [])


class TestKonkurensi(WalLoggerTestCase):
    def test_lsn_unik_dari_banyak_thread(self):
        jumlah_thread = 4
        per_thread = 25
        hasil: list[int] = []
        gembok = threading.Lock()

        def tulis(tx_base: int) -> None:
            for i in range(per_thread):
                lsn = self.logger.log_begin(tx_base + i)
                with gembok:
                    hasil.append(lsn)

        threads = [
            threading.Thread(target=tulis, args=(tid * 1000,))
            for tid in range(jumlah_thread)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(hasil), jumlah_thread * per_thread)
        self.assertEqual(len(set(hasil)), jumlah_thread * per_thread)
        self.assertEqual(len(self.reader.read_log(-1)), jumlah_thread * per_thread)

    def test_flush_paralel_tidak_rusak(self):
        self.logger.log_begin(1)
        threads = [
            threading.Thread(target=self.logger.flush_log_until, args=(1,))
            for _ in range(4)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(self.reader.read_log(-1)), 1)


class TestPath(WalLoggerTestCase):
    def test_direktori_induk_dibuat(self):
        target = Path(self._tmp.name) / "sub" / "log" / "wal.log"
        logger = WalLogger(str(target))
        self.addCleanup(logger.close)
        logger.log_begin(1)
        self.assertTrue(target.exists())

    def test_set_log_path_mengikuti_logger(self):
        self.assertEqual(codec.get_log_path(), self.path)


if __name__ == "__main__":
    unittest.main()
