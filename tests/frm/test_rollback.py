from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from common.log import LogKind
from common.storage import RID
from common.transaction import ChangeOp
from failure_recovery.results import RollbackResult
from failure_recovery.log.log_reader import LogReader
from failure_recovery.log.wal_logger import WalLogger
from failure_recovery.rollback.rollback_manager import RollbackManager
from storage_manager.facade import StorageManager


class RollbackManagerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.siapkan_manager_rollback()

    def siapkan_manager_rollback(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        log_path = Path(self.temp_dir.name) / "wal.log"
        self.writer = WalLogger(str(log_path))
        self.reader = LogReader(str(log_path))
        self.addCleanup(self.writer.close)
        self.sm = StorageManager(data_dir=self.temp_dir.name, buffer_frames=4)
        self.addCleanup(self.sm.close)
        self.tx_id = 7
        self.writer.log_begin(self.tx_id)
        self.manager = RollbackManager(
            self.writer,
            self.writer,
            self.reader,
            self.sm,
        )

    def tambahkan_perubahan_data(
        self,
        op: ChangeOp,
        rid: RID,
        before,
        after,
    ) -> None:
        self.writer.log_write(self.tx_id, op, rid, before, after)

    def test_rollback_insert_menghapus_record(self) -> None:
        rid = RID("student", 1, 2)
        self.tambahkan_perubahan_data(ChangeOp.INSERT, rid, [], [1, "A"])

        result = self.manager.rollback(self.tx_id)

        self.assertEqual(result, RollbackResult.DONE)
        self.assertEqual(
            [record.kind for record in self.reader.read_log(self.tx_id)],
            [LogKind.BEGIN, LogKind.DATA, LogKind.ABORT_REQUEST, LogKind.ABORT],
        )

    def test_rollback_update_memulihkan_before_image(self) -> None:
        rid = RID("student", 1, 2)
        self.tambahkan_perubahan_data(ChangeOp.UPDATE, rid, [1, "old"], [1, "new"])

        self.assertEqual(self.manager.rollback(self.tx_id), RollbackResult.DONE)

    def test_rollback_delete_memulihkan_before_image(self) -> None:
        rid = RID("student", 1, 2)
        self.tambahkan_perubahan_data(ChangeOp.DELETE, rid, [1, "old"], [])

        self.assertEqual(self.manager.rollback(self.tx_id), RollbackResult.DONE)

    def test_rollback_mengundo_perubahan_dengan_urutan_lsn_terbalik(self) -> None:
        first = RID("student", 1, 1)
        second = RID("student", 1, 2)
        self.tambahkan_perubahan_data(ChangeOp.UPDATE, first, [1], [2])
        self.tambahkan_perubahan_data(ChangeOp.DELETE, second, [3], [])

        self.assertEqual(self.manager.rollback(self.tx_id), RollbackResult.DONE)

    def test_transaksi_committed_tidak_dapat_di_rollback(self) -> None:
        self.writer.log_commit(self.tx_id)

        self.assertEqual(
            self.manager.rollback(self.tx_id),
            RollbackResult.COMMITTED,
        )
        self.assertEqual(
            [record.kind for record in self.reader.read_log(self.tx_id)],
            [LogKind.BEGIN, LogKind.COMMIT],
        )

    def test_transaksi_tidak_dikenal_menghasilkan_unknown_tx(self) -> None:
        self.assertEqual(
            self.manager.rollback(999),
            RollbackResult.UNKNOWN_TX,
        )

    def test_abort_selesai_menghasilkan_already_abort(self) -> None:
        self.writer.log_abort_end(self.tx_id)

        self.assertEqual(
            self.manager.rollback(self.tx_id),
            RollbackResult.ALREADY_ABORT,
        )
        self.assertEqual(
            [record.kind for record in self.reader.read_log(self.tx_id)],
            [LogKind.BEGIN, LogKind.ABORT],
        )

    def test_undo_dengan_storage_manager_asli_menghasilkan_done(self) -> None:
        rid = RID("student", 1, 2)
        self.tambahkan_perubahan_data(ChangeOp.UPDATE, rid, [1], [2])

        self.assertEqual(self.manager.rollback(self.tx_id), RollbackResult.DONE)

    def test_rollback_ulang_tidak_mengulangi_undo(self) -> None:
        rid = RID("student", 1, 2)
        self.tambahkan_perubahan_data(ChangeOp.INSERT, rid, [], [1])

        self.assertEqual(self.manager.rollback(self.tx_id), RollbackResult.DONE)
        self.assertEqual(
            self.manager.rollback(self.tx_id),
            RollbackResult.ALREADY_ABORT,
        )

    def test_record_abort_tersimpan_di_wal_asli(self) -> None:
        rid = RID("student", 1, 2)
        self.tambahkan_perubahan_data(ChangeOp.UPDATE, rid, [1], [2])

        self.manager.rollback(self.tx_id)

        self.assertEqual(
            [record.kind for record in self.reader.read_log(self.tx_id)],
            [LogKind.BEGIN, LogKind.DATA, LogKind.ABORT_REQUEST, LogKind.ABORT],
        )


if __name__ == "__main__":
    unittest.main()