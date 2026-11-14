"""Uji checkpoint manual, berkala, dan facade FR-06."""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import ANY, Mock

from common.errors import LogError, StorageError
from common.interfaces import ConcurrencyControlManager, StorageManager
from common.log import LogKind, RecoveryTarget
from failure_recovery import FailureRecoveryManager
from failure_recovery.checkpoint.checkpoint_manager import CheckpointManager
from failure_recovery.checkpoint.checkpoint_store import CheckpointStore
from failure_recovery.log.log_reader import LogReader
from failure_recovery.log.wal_logger import WalLogger


class CheckpointTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.logger = WalLogger(str(self.root / "wal.log"))
        self.addCleanup(self.logger.close)
        self.reader = LogReader(self.logger.log_path)
        self.log = Mock(wraps=self.logger)
        self.ccm = Mock(spec=ConcurrencyControlManager)
        self.sm = Mock(spec=StorageManager)
        self.store = CheckpointStore(str(self.root / "master.json"), str(self.root / "snapshots"))
        self.manager = CheckpointManager(
            self.log, self.ccm, self.sm, self.log, self.store, None,
            log_reader=self.reader, interval_seconds=0.01,
        )
        self.addCleanup(self.manager.stop_periodic)

    def _kinds(self):
        return [record.kind for record in self.reader.read_log(-1)]


class TestCheckpointManual(CheckpointTestCase):
    def test_urutan_wal_data_dan_penanda_selesai(self):
        trace = Mock()
        self.store.write_master_atomic = Mock(wraps=self.store.write_master_atomic)
        for name, method in (
            ("pause", self.ccm.pause_new_transactions),
            ("wait", self.ccm.wait_until_idle),
            ("begin", self.log.log_checkpoint_begin),
            ("flush_log", self.log.flush_log_until),
            ("flush_data", self.sm.flush),
            ("end", self.log.log_checkpoint_end),
            ("master", self.store.write_master_atomic),
            ("resume", self.ccm.resume_transactions),
        ):
            trace.attach_mock(method, name)

        result = self.manager.checkpoint()

        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.lsn, 2)
        self.assertEqual(
            [(call[0], call[1]) for call in trace.mock_calls],
            [
                ("pause", ()), ("wait", ()), ("begin", ()),
                ("flush_log", (1,)), ("flush_data", ()),
                ("end", ()), ("flush_log", (2,)),
                ("master", (ANY,)), ("resume", ()),
            ],
        )
        self.assertEqual(self._kinds(), [LogKind.CHECKPOINT_BEGIN, LogKind.CHECKPOINT_END])
        meta = self.store.find_latest_completed()
        self.assertEqual(meta.checkpoint_lsn, result.lsn)
        self.assertEqual(meta.active_tx, [])
        self.assertEqual(meta.snapshot_path, "")

    def test_restore_tanpa_pitr_gagal_tanpa_checkpoint(self):
        result = self.manager.restore_to(RecoveryTarget(tx_id=1))

        self.assertFalse(result.ok)
        self.assertIn("PITR", result.message)
        self.sm.flush.assert_not_called()
        self.assertEqual(self._kinds(), [])

    def test_log_lama_dan_max_tx_id_dipertahankan(self):
        self.logger.log_begin(17)
        self.logger.log_commit(17)
        self.logger.log_begin(3)
        self.logger.log_abort_end(3)
        before = self.reader.read_log(-1)

        self.assertTrue(self.manager.checkpoint().ok)

        self.assertEqual(self.reader.read_log(-1)[:len(before)], before)
        self.assertEqual(self.store.find_latest_completed().max_tx_id, 17)

    def test_gagal_pause_atau_wait_tetap_resume(self):
        for name in ("pause_new_transactions", "wait_until_idle"):
            with self.subTest(method=name):
                method = getattr(self.ccm, name)
                method.side_effect = RuntimeError("CCM gagal")
                result = self.manager.checkpoint()
                method.side_effect = None
                self.assertFalse(result.ok)
                self.assertIn("CCM gagal", result.message)
        self.assertEqual(self.ccm.resume_transactions.call_count, 2)
        self.sm.flush.assert_not_called()
        self.assertEqual(self._kinds(), [])
        self.assertIsNone(self.store.find_latest_completed())

    def test_gagal_begin_tidak_flush_data(self):
        self.log.log_checkpoint_begin.side_effect = LogError("gagal BEGIN")
        result = self.manager.checkpoint()
        self.assertFalse(result.ok)
        self.sm.flush.assert_not_called()
        self.ccm.resume_transactions.assert_called_once_with()
        self.assertIsNone(self.store.read_master())

    def test_gagal_flush_wal_tidak_menulis_data(self):
        self.log.flush_log_until.side_effect = LogError("gagal WAL")
        result = self.manager.checkpoint()
        self.assertFalse(result.ok)
        self.sm.flush.assert_not_called()
        self.log.log_checkpoint_end.assert_not_called()
        self.ccm.resume_transactions.assert_called_once_with()
        self.assertIsNone(self.store.read_master())

    def test_gagal_flush_data_tidak_menulis_checkpoint_end(self):
        self.sm.flush.side_effect = StorageError("gagal data")
        result = self.manager.checkpoint()
        self.assertFalse(result.ok)
        self.assertIn("gagal data", result.message)
        self.log.log_checkpoint_end.assert_not_called()
        self.ccm.resume_transactions.assert_called_once_with()
        self.assertEqual(self._kinds(), [LogKind.CHECKPOINT_BEGIN])
        self.assertIsNone(self.store.find_latest_completed())

    def test_gagal_end_tidak_memperbarui_master(self):
        self.assertTrue(self.manager.checkpoint().ok)
        before = self.store.read_master()
        self.log.log_checkpoint_end.side_effect = LogError("gagal END")
        self.assertFalse(self.manager.checkpoint().ok)
        self.assertEqual(self.store.read_master(), before)
        self.assertEqual(self.ccm.resume_transactions.call_count, 2)

    def test_gagal_flush_end_mempertahankan_checkpoint_lama(self):
        self.assertTrue(self.manager.checkpoint().ok)
        before = self.store.read_master()
        self.log.flush_log_until.side_effect = [None, LogError("gagal flush END")]
        self.assertFalse(self.manager.checkpoint().ok)
        self.assertEqual(self.store.find_latest_completed(), before)
        self.assertEqual(self.ccm.resume_transactions.call_count, 2)

    def test_gagal_master_tetap_resume_dan_bisa_diulang(self):
        original = self.store.write_master_atomic
        self.store.write_master_atomic = Mock(side_effect=LogError("gagal master"))
        result = self.manager.checkpoint()
        self.assertFalse(result.ok)
        self.assertIsNone(self.store.read_master())
        self.ccm.resume_transactions.assert_called_once_with()
        self.store.write_master_atomic = original
        self.assertTrue(self.manager.checkpoint().ok)

    def test_gagal_resume_menghasilkan_status_gagal(self):
        self.ccm.resume_transactions.side_effect = RuntimeError("gagal resume")
        result = self.manager.checkpoint()
        self.assertFalse(result.ok)
        self.assertIn("gagal resume", result.message)
        self.assertEqual(self.store.find_latest_completed().checkpoint_lsn, result.lsn)

    def test_checkpoint_bersamaan_dijalankan_bergantian(self):
        entered, release, second_pause = threading.Event(), threading.Event(), threading.Event()
        results = []
        workers = [threading.Thread(target=lambda: results.append(self.manager.checkpoint())) for _ in range(2)]

        def pause():
            if self.ccm.pause_new_transactions.call_count == 2:
                second_pause.set()

        self.ccm.pause_new_transactions.side_effect = pause

        def flush():
            entered.set()
            if not release.wait(2):
                raise RuntimeError("uji flush tidak dilepas")

        self.sm.flush.side_effect = flush
        try:
            workers[0].start()
            self.assertTrue(entered.wait(1))
            workers[1].start()
            self.assertFalse(second_pause.wait(0.1))
            self.assertEqual(self.sm.flush.call_count, 1)
            self.assertEqual(self.ccm.pause_new_transactions.call_count, 1)
        finally:
            release.set()
            for worker in workers:
                if worker.ident is not None:
                    worker.join(2)
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(len(results), 2)
        self.assertTrue(all(result.ok for result in results))
        self.assertEqual(self._kinds(), [
            LogKind.CHECKPOINT_BEGIN, LogKind.CHECKPOINT_END,
            LogKind.CHECKPOINT_BEGIN, LogKind.CHECKPOINT_END,
        ])


class TestCheckpointBerkala(CheckpointTestCase):
    def test_start_stop_idempoten_dan_bisa_dimulai_ulang(self):
        done = threading.Event()
        self.sm.flush.side_effect = done.set
        self.manager.start_periodic()
        worker = self.manager.scheduler_thread
        self.manager.start_periodic()
        self.assertIs(self.manager.scheduler_thread, worker)
        self.assertTrue(worker.daemon)
        self.assertTrue(done.wait(2))
        self.manager.stop_periodic()
        self.manager.stop_periodic()
        self.assertFalse(worker.is_alive())
        self.assertIsNone(self.manager.scheduler_thread)
        self.assertIsNotNone(self.store.find_latest_completed())

        done.clear()
        self.manager.start_periodic()
        self.assertIsNot(self.manager.scheduler_thread, worker)
        self.assertTrue(done.wait(2))
        self.manager.stop_periodic()

    def test_gagal_checkpoint_tidak_menghentikan_jadwal(self):
        done = threading.Event()
        attempts = []

        def flush():
            attempts.append(1)
            if len(attempts) == 1:
                raise StorageError("disk sementara gagal")
            done.set()

        self.sm.flush.side_effect = flush
        with self.assertLogs("coachdb.frm", level="WARNING") as output:
            try:
                self.manager.start_periodic()
                self.assertTrue(done.wait(2))
            finally:
                self.manager.stop_periodic()
        self.assertIn("disk sementara gagal", " ".join(output.output))
        self.assertIsNotNone(self.store.find_latest_completed())

    def test_interval_tidak_valid_ditolak(self):
        for interval in (0, -1, float("inf"), float("nan")):
            with self.subTest(interval=interval), self.assertRaises(ValueError):
                CheckpointManager(self.log, self.ccm, self.sm, self.log, self.store, None,
                                  interval_seconds=interval)


class TestFacadeCheckpoint(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.path = self.root / "wal.log"
        self.master = self.root / "master.json"
        self.frm = FailureRecoveryManager(log_path=str(self.path), master_path=str(self.master))
        self.addCleanup(self.frm.close)
        self.sm = Mock(spec=StorageManager)
        self.ccm = Mock(spec=ConcurrencyControlManager)

    def test_checkpoint_tanpa_dependensi_menghasilkan_status_gagal(self):
        result = self.frm.checkpoint()
        self.assertFalse(result.ok)
        self.assertIn("dependensi", result.message.lower())
        self.assertEqual(self.frm.read_log(), [])

    def test_facade_mendelegasikan_checkpoint_dan_metadata_persisten(self):
        self.frm.set_dependencies(self.sm, self.ccm)
        result = self.frm.checkpoint()
        self.assertTrue(result.ok, result.message)
        self.sm.flush.assert_called_once_with()
        self.ccm.pause_new_transactions.assert_called_once_with()
        self.ccm.wait_until_idle.assert_called_once_with()
        self.ccm.resume_transactions.assert_called_once_with()
        self.assertEqual([r.kind for r in self.frm.read_log()], [
            LogKind.CHECKPOINT_BEGIN, LogKind.CHECKPOINT_END,
        ])
        self.frm.close()
        store = CheckpointStore(str(self.master), str(self.root / "snapshots"))
        self.assertEqual(store.find_latest_completed().checkpoint_lsn, result.lsn)

    def test_master_default_berada_di_samping_wal(self):
        frm = FailureRecoveryManager(log_path=str(self.root / "custom.log"))
        self.addCleanup(frm.close)
        frm.set_dependencies(self.sm, self.ccm)
        self.assertTrue(frm.checkpoint().ok)
        self.assertTrue((self.root / "custom.checkpoint.json").is_file())

    def test_close_menghentikan_scheduler_sebelum_logger(self):
        self.frm.set_dependencies(self.sm, self.ccm)
        manager = self.frm._checkpoint_service
        manager.interval_seconds = 0.01
        done = threading.Event()
        self.sm.flush.side_effect = done.set
        manager.start_periodic()
        worker = manager.scheduler_thread
        self.assertTrue(done.wait(2))
        self.frm.close()
        self.assertFalse(worker.is_alive())
        self.assertFalse(self.frm.flush_log(999))


if __name__ == "__main__":
    unittest.main()
