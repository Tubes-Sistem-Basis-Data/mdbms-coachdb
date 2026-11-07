from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from common.log import LogKind
from common.storage import RID
from common.transaction import NO_RECORD, ChangeOp
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


class FakeStorageManager:
    """SM in-memory untuk memverifikasi efek undo, bukan sekadar nilai balik."""

    def __init__(self) -> None:
        self.rows: dict[RID, list] = {}
        self.calls: list[tuple] = []

    def insert(self, table: str, rid: RID, values: list, lsn: int | None) -> None:
        self.calls.append(("insert", rid, list(values)))
        self.rows[rid] = list(values)

    def update(self, table: str, rid: RID, values: list, lsn: int | None) -> None:
        self.calls.append(("update", rid, list(values)))
        self.rows[rid] = list(values)

    def delete(self, table: str, rid: RID, lsn: int | None) -> None:
        self.calls.append(("delete", rid))
        self.rows.pop(rid, None)


class RollbackManagerEfekTest(unittest.TestCase):
    """Memastikan rollback benar-benar mengubah isi SM, bukan hanya DONE."""

    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        log_path = Path(self.temp_dir.name) / "wal.log"
        self.writer = WalLogger(str(log_path))
        self.reader = LogReader(str(log_path))
        self.addCleanup(self.writer.close)
        self.sm = FakeStorageManager()
        self.tx_id = 7
        self.writer.log_begin(self.tx_id)
        self.manager = RollbackManager(
            self.writer,
            self.writer,
            self.reader,
            self.sm,
        )

    def catat(self, op: ChangeOp, rid: RID, before, after) -> None:
        self.writer.log_write(self.tx_id, op, rid, before, after)

    def test_undo_insert_menghapus_baris_dari_sm(self) -> None:
        rid = RID("student", 1, 1)
        self.sm.rows[rid] = [1, "A"]
        self.catat(ChangeOp.INSERT, rid, NO_RECORD, [1, "A"])

        self.assertEqual(self.manager.rollback(self.tx_id), RollbackResult.DONE)
        self.assertNotIn(rid, self.sm.rows)
        self.assertEqual(self.sm.calls, [("delete", rid)])

    def test_undo_update_mengembalikan_before_image_di_sm(self) -> None:
        rid = RID("student", 1, 1)
        self.sm.rows[rid] = [1, "new"]
        self.catat(ChangeOp.UPDATE, rid, [1, "old"], [1, "new"])

        self.assertEqual(self.manager.rollback(self.tx_id), RollbackResult.DONE)
        self.assertEqual(self.sm.rows[rid], [1, "old"])
        self.assertEqual(self.sm.calls, [("update", rid, [1, "old"])])

    def test_undo_delete_mengembalikan_baris_di_sm(self) -> None:
        rid = RID("student", 1, 1)
        self.catat(ChangeOp.DELETE, rid, [1, "old"], NO_RECORD)

        self.assertEqual(self.manager.rollback(self.tx_id), RollbackResult.DONE)
        self.assertEqual(self.sm.rows[rid], [1, "old"])
        self.assertEqual(self.sm.calls, [("insert", rid, [1, "old"])])

    def test_urutan_undo_terbalik_di_sm(self) -> None:
        rid = RID("student", 1, 1)
        self.sm.rows[rid] = [2]
        self.catat(ChangeOp.UPDATE, rid, [0], [1])
        self.catat(ChangeOp.UPDATE, rid, [1], [2])

        self.assertEqual(self.manager.rollback(self.tx_id), RollbackResult.DONE)
        self.assertEqual(self.sm.rows[rid], [0])
        self.assertEqual(
            self.sm.calls,
            [("update", rid, [1]), ("update", rid, [0])],
        )

    def test_rollback_mengembalikan_kondisi_awal_semua_operasi(self) -> None:
        baru = RID("student", 1, 1)
        ubah = RID("student", 1, 2)
        hapus = RID("student", 1, 3)
        self.sm.rows[ubah] = [10, "B"]
        self.sm.rows[hapus] = [30, "C"]
        self.catat(ChangeOp.INSERT, baru, NO_RECORD, [1, "A"])
        self.catat(ChangeOp.UPDATE, ubah, [10, "B"], [11, "B"])
        self.catat(ChangeOp.DELETE, hapus, [30, "C"], NO_RECORD)
        self.sm.rows[baru] = [1, "A"]
        self.sm.rows[ubah] = [11, "B"]
        self.sm.rows.pop(hapus)

        self.assertEqual(self.manager.rollback(self.tx_id), RollbackResult.DONE)
        self.assertNotIn(baru, self.sm.rows)
        self.assertEqual(self.sm.rows[ubah], [10, "B"])
        self.assertEqual(self.sm.rows[hapus], [30, "C"])

    def test_resume_setelah_abort_request_tidak_menulis_request_ganda(self) -> None:
        rid = RID("student", 1, 1)
        self.catat(ChangeOp.INSERT, rid, NO_RECORD, [1])
        self.writer.log_abort_request(self.tx_id)
        self.sm.rows[rid] = [1]

        self.assertEqual(self.manager.rollback(self.tx_id), RollbackResult.DONE)
        self.assertNotIn(rid, self.sm.rows)
        self.assertEqual(
            [record.kind for record in self.reader.read_log(self.tx_id)],
            [LogKind.BEGIN, LogKind.DATA, LogKind.ABORT_REQUEST, LogKind.ABORT],
        )

    def test_rollback_tanpa_data_tetap_menulis_abort(self) -> None:
        self.assertEqual(self.manager.rollback(self.tx_id), RollbackResult.DONE)
        self.assertEqual(self.sm.calls, [])
        self.assertEqual(
            [record.kind for record in self.reader.read_log(self.tx_id)],
            [LogKind.BEGIN, LogKind.ABORT_REQUEST, LogKind.ABORT],
        )

    def test_transaksi_committed_tidak_menyentuh_sm(self) -> None:
        self.writer.log_commit(self.tx_id)

        self.assertEqual(
            self.manager.rollback(self.tx_id),
            RollbackResult.COMMITTED,
        )
        self.assertEqual(self.sm.calls, [])

    def test_transaksi_tidak_dikenal_tidak_menyentuh_sm(self) -> None:
        self.assertEqual(
            self.manager.rollback(999),
            RollbackResult.UNKNOWN_TX,
        )
        self.assertEqual(self.sm.calls, [])
        self.assertEqual(
            [record.kind for record in self.reader.read_log(self.tx_id)],
            [LogKind.BEGIN],
        )


if __name__ == "__main__":
    unittest.main()