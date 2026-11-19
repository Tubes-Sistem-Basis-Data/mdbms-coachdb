"""Uji koordinasi checkpoint dengan SM, FRM, dan CCM asli."""

from __future__ import annotations

import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from common.log import LogKind
from common.transaction import ProtocolType
from concurrency_control.manager import CCManager
from failure_recovery import FailureRecoveryManager
from failure_recovery.checkpoint.checkpoint_store import CheckpointStore
from storage_manager.facade import StorageManager


class TestCheckpointRealCCM(unittest.TestCase):
    """Checkpoint menunggu transaksi lama dan menahan transaksi baru."""

    def test_checkpoint_menunggu_commit(self) -> None:
        self._run_protocols("commit")

    def test_checkpoint_menunggu_abort(self) -> None:
        self._run_protocols("abort")

    def _run_protocols(self, finish: str) -> None:
        for protocol in (ProtocolType.LOCK_BASED, ProtocolType.TIMESTAMP_BASED):
            for attempt in range(3):
                with self.subTest(protocol=protocol.value, finish=finish, attempt=attempt):
                    self._checkpoint_with_active_transaction(protocol, finish)

    def _checkpoint_with_active_transaction(
        self, protocol: ProtocolType, finish: str,
    ) -> None:
        with ExitStack() as stack:
            root = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="frm-checkpoint-")))
            master = root / "master.json"
            snapshots = root / "snapshots"
            frm = FailureRecoveryManager(
                log_path=str(root / "wal.log"), master_path=str(master),
                snapshot_dir=str(snapshots),
            )
            stack.callback(frm.close)
            sm = StorageManager(str(root))
            stack.callback(sm.close)
            ccm = CCManager(frm, protocol=protocol)
            frm.set_dependencies(sm, ccm)
            sm.set_log_flusher(frm.flush_log)

            checkpoint_waiting = threading.Event()
            transaction_waiting = threading.Event()
            flushing = threading.Event()
            release_flush = threading.Event()
            results = []
            new_transactions = []
            errors = []
            tx_id = ccm.begin_transaction()

            def checkpoint() -> None:
                try:
                    results.append(frm.checkpoint())
                except Exception as exc:
                    errors.append(exc)

            def begin_transaction() -> None:
                try:
                    new_tx_id = ccm.begin_transaction()
                    new_transactions.append(new_tx_id)
                    result = ccm.commit(new_tx_id)
                    self.assertTrue(result.ok, result.message)
                except Exception as exc:
                    errors.append(exc)

            checkpoint_worker = threading.Thread(target=checkpoint, name="test-checkpoint")
            transaction_worker = threading.Thread(target=begin_transaction, name="test-begin")
            original_wait = ccm._cond.wait
            original_flush = sm.flush

            def observe_wait(timeout=None):
                # Amati wait sebenarnya tanpa mengganti perilaku CCM.
                if threading.current_thread() is checkpoint_worker:
                    checkpoint_waiting.set()
                elif threading.current_thread() is transaction_worker:
                    transaction_waiting.set()
                return original_wait(timeout)

            def hold_flush() -> None:
                flushing.set()
                if not release_flush.wait(5):
                    raise TimeoutError("Test belum melepas flush checkpoint")
                original_flush()

            with patch.object(ccm._cond, "wait", new=observe_wait), \
                    patch.object(sm, "flush", new=hold_flush):
                try:
                    checkpoint_worker.start()
                    self.assertTrue(checkpoint_waiting.wait(5), "Checkpoint belum menunggu T1")
                    transaction_worker.start()
                    self.assertTrue(transaction_waiting.wait(5), "T2 belum tertahan oleh pause")
                    self.assertEqual(results, [])
                    self.assertEqual(new_transactions, [])
                    self.assertFalse(flushing.is_set())
                    self.assertFalse(master.exists())
                    self.assertFalse(any(
                        record.kind is LogKind.CHECKPOINT_BEGIN for record in frm.read_log()
                    ))

                    finished = getattr(ccm, finish)(tx_id)
                    self.assertTrue(finished.ok, finished.message)
                    self.assertTrue(flushing.wait(5), "Checkpoint belum lanjut setelah T1 selesai")
                    self.assertEqual(new_transactions, [], "T2 berjalan sebelum checkpoint selesai")
                    self.assertEqual(results, [])
                    release_flush.set()
                    checkpoint_worker.join(5)
                    transaction_worker.join(5)
                    self.assertFalse(checkpoint_worker.is_alive(), "Thread checkpoint menggantung")
                    self.assertFalse(transaction_worker.is_alive(), "Thread T2 menggantung")
                    self.assertEqual(errors, [])
                finally:
                    release_flush.set()
                    ccm.abort(tx_id)
                    ccm.resume_transactions()
                    for worker in (checkpoint_worker, transaction_worker):
                        if worker.ident is not None:
                            worker.join(5)
                    for new_tx_id in new_transactions:
                        ccm.abort(new_tx_id)

            self.assertFalse(checkpoint_worker.is_alive())
            self.assertFalse(transaction_worker.is_alive())
            self.assertEqual(len(results), 1)
            self.assertTrue(results[0].ok, results[0].message)
            self.assertEqual(len(new_transactions), 1)
            records = frm.read_log()
            markers = [record for record in records if record.kind in (
                LogKind.CHECKPOINT_BEGIN, LogKind.CHECKPOINT_END,
            )]
            self.assertEqual([record.kind for record in markers], [
                LogKind.CHECKPOINT_BEGIN, LogKind.CHECKPOINT_END,
            ])
            finished_kind = LogKind.COMMIT if finish == "commit" else LogKind.ABORT
            finished_record = next(record for record in records
                                   if record.tx_id == tx_id and record.kind is finished_kind)
            new_begin = next(record for record in records
                             if record.tx_id == new_transactions[0] and record.kind is LogKind.BEGIN)
            self.assertLess(finished_record.lsn, markers[0].lsn)
            self.assertLess(markers[1].lsn, new_begin.lsn)
            self.assertEqual(results[0].lsn, markers[1].lsn)

            metadata = CheckpointStore(str(master), str(snapshots)).find_latest_completed()
            self.assertIsNotNone(metadata)
            self.assertEqual(metadata.checkpoint_lsn, results[0].lsn)
            self.assertEqual(metadata.active_tx, [])
            self.assertEqual(metadata.max_tx_id, tx_id)
            self.assertTrue((Path(metadata.snapshot_path) / "catalog.json").is_file())
        self.assertFalse(root.exists())


if __name__ == "__main__":
    unittest.main()
