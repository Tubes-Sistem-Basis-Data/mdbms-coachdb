"""Uji crash recovery otomatis FR-07 dengan beberapa transaksi."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from common.log import LogKind, LogRecord
from common.storage import RID
from common.transaction import NO_RECORD, ChangeOp, DataChange
from failure_recovery import FailureRecoveryManager


class FakeStorageManager:
    """SM in-memory untuk mengamati efek redo dan undo."""

    def __init__(self, rows: dict[RID, list] | None = None) -> None:
        self.rows = dict(rows or {})
        self.calls: list[str] = []

    def insert(self, table: str, rid: RID, values: list, lsn: int | None) -> None:
        self.calls.append(f"insert:{rid.table}:{rid.page_id}:{rid.slot}")
        self.rows[rid] = list(values)

    def update(self, table: str, rid: RID, values: list, lsn: int | None) -> None:
        self.calls.append(f"update:{rid.table}:{rid.page_id}:{rid.slot}")
        self.rows[rid] = list(values)

    def delete(self, table: str, rid: RID, lsn: int | None) -> None:
        self.calls.append(f"delete:{rid.table}:{rid.page_id}:{rid.slot}")
        self.rows.pop(rid, None)

    def flush(self, page_ids: list[int] | None = None) -> None:
        self.calls.append("flush")

    def rebuild_indexes(self) -> None:
        self.calls.append("rebuild_indexes")


class FakeConcurrencyControl:
    """CCM minimal untuk checkpoint setelah recovery."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def pause_new_transactions(self) -> None:
        self.calls.append("pause")

    def wait_until_idle(self) -> None:
        self.calls.append("wait")

    def resume_transactions(self) -> None:
        self.calls.append("resume")


class RecoveryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "wal.log"
        self.frm = FailureRecoveryManager(log_path=str(self.path))
        self.addCleanup(self.frm.close)

    def _change(self, rid: RID, op: ChangeOp, before, after) -> DataChange:
        return DataChange(rid.table, rid, op, before, after)

    def _write(self, kind: LogKind, tx_id: int, change: DataChange | None = None) -> int:
        result = self.frm.write_log(LogRecord(kind, tx_id=tx_id, change=change))
        self.assertTrue(result.ok, result.message)
        return result.lsn

    def _reopen(self) -> FailureRecoveryManager:
        self.frm.close()
        self.frm = FailureRecoveryManager(log_path=str(self.path))
        self.addCleanup(self.frm.close)
        return self.frm

    def _prepare_recovery(self, rows: dict[RID, list] | None = None):
        frm = self._reopen()
        sm = FakeStorageManager(rows)
        ccm = FakeConcurrencyControl()
        frm.set_dependencies(sm, ccm)
        return frm, sm, ccm


class TestRedo(RecoveryTestCase):
    def test_redo_insert_transaksi_commit(self) -> None:
        rid = RID("student", 0, 1)
        self._write(LogKind.BEGIN, 1)
        self._write(LogKind.DATA, 1, self._change(rid, ChangeOp.INSERT, NO_RECORD, [1]))
        last_lsn = self._write(LogKind.COMMIT, 1)
        self.assertTrue(self.frm.flush_log(last_lsn))

        frm, sm, _ = self._prepare_recovery()

        result = frm.recover()

        self.assertTrue(result.ok, result.message)
        self.assertEqual(sm.rows.get(rid), [1])
        self.assertGreaterEqual(result.last_tx_id, 1)
        self.assertGreaterEqual(result.last_lsn, last_lsn)

    def test_redo_update_dan_delete_transaksi_commit(self) -> None:
        update_rid = RID("student", 0, 1)
        delete_rid = RID("student", 0, 2)
        self._write(LogKind.BEGIN, 1)
        self._write(
            LogKind.DATA, 1,
            self._change(update_rid, ChangeOp.UPDATE, [1, "old"], [1, "new"]),
        )
        self._write(
            LogKind.DATA, 1,
            self._change(delete_rid, ChangeOp.DELETE, [2, "old"], NO_RECORD),
        )
        last_lsn = self._write(LogKind.COMMIT, 1)
        self.assertTrue(self.frm.flush_log(last_lsn))

        frm, sm, _ = self._prepare_recovery({delete_rid: [2, "old"]})

        result = frm.recover()

        self.assertTrue(result.ok, result.message)
        self.assertEqual(sm.rows.get(update_rid), [1, "new"])
        self.assertNotIn(delete_rid, sm.rows)


class TestUndo(RecoveryTestCase):
    def test_undo_insert_transaksi_belum_commit(self) -> None:
        rid = RID("student", 0, 1)
        self._write(LogKind.BEGIN, 2)
        self._write(LogKind.DATA, 2, self._change(rid, ChangeOp.INSERT, NO_RECORD, [2]))
        self.assertTrue(self.frm.flush_log(2))

        frm, sm, _ = self._prepare_recovery({rid: [2]})

        result = frm.recover()

        self.assertTrue(result.ok, result.message)
        self.assertNotIn(rid, sm.rows)

    def test_transaksi_sudah_abort_tidak_di_undo_ulang(self) -> None:
        rid = RID("student", 0, 1)
        self._write(LogKind.BEGIN, 3)
        self._write(LogKind.DATA, 3, self._change(rid, ChangeOp.INSERT, NO_RECORD, [3]))
        self._write(LogKind.ABORT_REQUEST, 3)
        last_lsn = self._write(LogKind.ABORT, 3)
        self.assertTrue(self.frm.flush_log(last_lsn))

        frm, sm, _ = self._prepare_recovery()

        result = frm.recover()

        self.assertTrue(result.ok, result.message)
        self.assertNotIn(rid, sm.rows)
        self.assertFalse(any(call.startswith("insert:") for call in sm.calls))


class TestMultiTransaksi(RecoveryTestCase):
    def test_redo_commit_dan_undo_uncommitted(self) -> None:
        committed_insert = RID("student", 0, 1)
        uncommitted_insert = RID("student", 0, 2)
        committed_update = RID("student", 0, 3)

        self._write(LogKind.BEGIN, 1)
        self._write(
            LogKind.DATA, 1,
            self._change(committed_insert, ChangeOp.INSERT, NO_RECORD, [1]),
        )
        self._write(LogKind.COMMIT, 1)

        self._write(LogKind.BEGIN, 2)
        self._write(
            LogKind.DATA, 2,
            self._change(uncommitted_insert, ChangeOp.INSERT, NO_RECORD, [2]),
        )

        self._write(LogKind.BEGIN, 3)
        self._write(
            LogKind.DATA, 3,
            self._change(
                committed_update, ChangeOp.UPDATE, [3, "old"], [3, "new"]
            ),
        )
        last_lsn = self._write(LogKind.COMMIT, 3)
        self.assertTrue(self.frm.flush_log(last_lsn))

        frm, sm, _ = self._prepare_recovery({
            uncommitted_insert: [2],
            committed_update: [3, "old"],
        })

        result = frm.recover()

        self.assertTrue(result.ok, result.message)
        self.assertEqual(sm.rows.get(committed_insert), [1])
        self.assertNotIn(uncommitted_insert, sm.rows)
        self.assertEqual(sm.rows.get(committed_update), [3, "new"])
        self.assertGreaterEqual(result.last_tx_id, 3)

    def test_recovery_diulang_tetap_idempoten(self) -> None:
        rid = RID("student", 0, 1)
        self._write(LogKind.BEGIN, 1)
        self._write(LogKind.DATA, 1, self._change(rid, ChangeOp.INSERT, NO_RECORD, [1]))
        last_lsn = self._write(LogKind.COMMIT, 1)
        self.assertTrue(self.frm.flush_log(last_lsn))

        frm, sm, _ = self._prepare_recovery()
        first = frm.recover()
        calls_after_first = list(sm.calls)
        second = frm.recover()

        self.assertTrue(first.ok, first.message)
        self.assertTrue(second.ok, second.message)
        self.assertEqual(sm.rows.get(rid), [1])
        self.assertEqual(calls_after_first.count("insert:student:0:1"), 1)
        self.assertNotIn("insert:student:0:1", sm.calls[len(calls_after_first):])


class TestCheckpointDanKosong(RecoveryTestCase):
    def test_recovery_mulai_dari_checkpoint_terakhir(self) -> None:
        first_rid = RID("student", 0, 1)
        second_rid = RID("student", 0, 2)

        self._write(LogKind.BEGIN, 1)
        self._write(LogKind.DATA, 1, self._change(first_rid, ChangeOp.INSERT, NO_RECORD, [1]))
        last_lsn = self._write(LogKind.COMMIT, 1)
        self.assertTrue(self.frm.flush_log(last_lsn))

        sm_before = FakeStorageManager({first_rid: [1]})
        self.frm.set_dependencies(sm_before, FakeConcurrencyControl())
        checkpoint = self.frm.checkpoint()
        self.assertTrue(checkpoint.ok, checkpoint.message)

        self._write(LogKind.BEGIN, 2)
        self._write(LogKind.DATA, 2, self._change(second_rid, ChangeOp.INSERT, NO_RECORD, [2]))
        last_lsn = self._write(LogKind.COMMIT, 2)
        self.assertTrue(self.frm.flush_log(last_lsn))

        frm, sm, _ = self._prepare_recovery({first_rid: [1]})

        result = frm.recover()

        self.assertTrue(result.ok, result.message)
        self.assertEqual(sm.rows.get(first_rid), [1])
        self.assertEqual(sm.rows.get(second_rid), [2])
        self.assertEqual(sm.calls.count("insert:student:0:1"), 0)
        self.assertEqual(sm.calls.count("insert:student:0:2"), 1)

    def test_recovery_membangun_ulang_index_sebelum_checkpoint(self) -> None:
        rid = RID("student", 0, 1)
        self._write(LogKind.BEGIN, 1)
        self._write(LogKind.DATA, 1, self._change(rid, ChangeOp.INSERT, NO_RECORD, [1]))
        last_lsn = self._write(LogKind.COMMIT, 1)
        self.assertTrue(self.frm.flush_log(last_lsn))

        frm, sm, _ = self._prepare_recovery()

        result = frm.recover()

        self.assertTrue(result.ok, result.message)
        self.assertEqual(sm.calls.count("rebuild_indexes"), 1)
        last_flush = len(sm.calls) - 1 - sm.calls[::-1].index("flush")
        self.assertLess(sm.calls.index("rebuild_indexes"), last_flush)

    def test_wal_kosong_recovery_sukses(self) -> None:
        frm, sm, _ = self._prepare_recovery()

        result = frm.recover()

        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.last_tx_id, 0)
        self.assertEqual(sm.rows, {})


if __name__ == "__main__":
    unittest.main()
