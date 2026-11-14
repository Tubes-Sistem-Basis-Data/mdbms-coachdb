"""Uji point-in-time recovery FR-05."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from common.log import LogKind, LogRecord, RecoveryTarget
from common.storage import RID
from common.transaction import NO_RECORD, ChangeOp, DataChange
from failure_recovery import FailureRecoveryManager


class FakeStorageManager:
    """SM in-memory untuk mengamati efek undo PITR."""

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


class FakeConcurrencyControl:
    """CCM minimal untuk checkpoint setelah restore."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def pause_new_transactions(self) -> None:
        self.calls.append("pause")

    def wait_until_idle(self) -> None:
        self.calls.append("wait")

    def resume_transactions(self) -> None:
        self.calls.append("resume")


class PITRTestCase(unittest.TestCase):
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

    def _dependencies(self, rows: dict[RID, list] | None = None):
        sm = FakeStorageManager(rows)
        ccm = FakeConcurrencyControl()
        self.frm.set_dependencies(sm, ccm)
        return sm, ccm

    def _restore_positions(self) -> list[int]:
        return [
            index
            for index, record in enumerate(self.frm.read_log())
            if record.kind is LogKind.RESTORE
        ]


class TestRestoreTarget(PITRTestCase):
    def test_restore_to_tx_mengembalikan_before_image_dan_checkpoint(self) -> None:
        rid = RID("student", 0, 1)
        sm, _ = self._dependencies({rid: [1, "old"]})
        self._write(LogKind.BEGIN, 1)
        self._write(
            LogKind.DATA, 1,
            self._change(rid, ChangeOp.INSERT, NO_RECORD, [1, "old"]),
        )
        self._write(LogKind.COMMIT, 1)
        self.assertTrue(self.frm.checkpoint().ok)

        self._write(LogKind.BEGIN, 2)
        self._write(
            LogKind.DATA, 2,
            self._change(rid, ChangeOp.UPDATE, [1, "old"], [1, "new"]),
        )
        self._write(LogKind.COMMIT, 2)
        sm.rows[rid] = [1, "new"]

        result = self.frm.recover_to(RecoveryTarget(tx_id=1))

        self.assertTrue(result.ok, result.message)
        self.assertEqual(sm.rows.get(rid), [1, "old"])
        records = self.frm.read_log()
        restore_positions = self._restore_positions()
        self.assertTrue(restore_positions)
        self.assertTrue(any(
            record.kind is LogKind.CHECKPOINT_END
            for record in records[restore_positions[-1] + 1:]
        ))
        self.assertGreaterEqual(result.last_tx_id, 2)
        self.assertGreaterEqual(result.last_lsn, records[-1].lsn)

    def test_restore_to_time_mengembalikan_state_sebelum_update(self) -> None:
        rid = RID("student", 0, 1)
        sm, _ = self._dependencies({rid: [1, "old"]})
        self._write(LogKind.BEGIN, 1)
        self._write(
            LogKind.DATA, 1,
            self._change(rid, ChangeOp.INSERT, NO_RECORD, [1, "old"]),
        )
        self._write(LogKind.COMMIT, 1)
        first_commit = self.frm.read_log(1)[-1]
        time.sleep(0.01)

        self._write(LogKind.BEGIN, 2)
        self._write(
            LogKind.DATA, 2,
            self._change(rid, ChangeOp.UPDATE, [1, "old"], [1, "new"]),
        )
        self._write(LogKind.COMMIT, 2)
        second_change = next(
            record for record in self.frm.read_log(2)
            if record.kind is LogKind.DATA
        )
        sm.rows[rid] = [1, "new"]
        target = (first_commit.timestamp + second_change.timestamp) / 2

        result = self.frm.recover_to(RecoveryTarget(timestamp=target))

        self.assertTrue(result.ok, result.message)
        self.assertEqual(sm.rows.get(rid), [1, "old"])
        self.assertTrue(self._restore_positions())

    def test_restore_to_time_mengundo_transaksi_belum_commit(self) -> None:
        rid = RID("student", 0, 1)
        sm, _ = self._dependencies({rid: [1, "old"]})
        self._write(LogKind.BEGIN, 1)
        self._write(
            LogKind.DATA, 1,
            self._change(rid, ChangeOp.INSERT, NO_RECORD, [1, "old"]),
        )
        self._write(LogKind.COMMIT, 1)

        self._write(LogKind.BEGIN, 2)
        self._write(
            LogKind.DATA, 2,
            self._change(rid, ChangeOp.UPDATE, [1, "old"], [1, "new"]),
        )
        sm.rows[rid] = [1, "new"]
        time.sleep(0.01)

        result = self.frm.recover_to(RecoveryTarget(timestamp=time.time()))

        self.assertTrue(result.ok, result.message)
        self.assertEqual(sm.rows.get(rid), [1, "old"])

    def test_restore_mengundo_insert_dan_delete(self) -> None:
        rid_a = RID("student", 0, 1)
        rid_b = RID("student", 0, 2)
        sm, _ = self._dependencies({rid_a: [1]})
        self._write(LogKind.BEGIN, 1)
        self._write(
            LogKind.DATA, 1,
            self._change(rid_a, ChangeOp.INSERT, NO_RECORD, [1]),
        )
        self._write(LogKind.COMMIT, 1)

        self._write(LogKind.BEGIN, 2)
        self._write(
            LogKind.DATA, 2,
            self._change(rid_a, ChangeOp.DELETE, [1], NO_RECORD),
        )
        self._write(
            LogKind.DATA, 2,
            self._change(rid_b, ChangeOp.INSERT, NO_RECORD, [2]),
        )
        self._write(LogKind.COMMIT, 2)
        sm.rows = {rid_b: [2]}

        result = self.frm.recover_to(RecoveryTarget(tx_id=1))

        self.assertTrue(result.ok, result.message)
        self.assertEqual(sm.rows, {rid_a: [1]})

    def test_restore_diulang_ke_target_sama_tetap_konsisten(self) -> None:
        rid = RID("student", 0, 1)
        sm, _ = self._dependencies({rid: [1, "old"]})
        self._write(LogKind.BEGIN, 1)
        self._write(
            LogKind.DATA, 1,
            self._change(rid, ChangeOp.INSERT, NO_RECORD, [1, "old"]),
        )
        self._write(LogKind.COMMIT, 1)
        self._write(LogKind.BEGIN, 2)
        self._write(
            LogKind.DATA, 2,
            self._change(rid, ChangeOp.UPDATE, [1, "old"], [1, "new"]),
        )
        self._write(LogKind.COMMIT, 2)
        sm.rows[rid] = [1, "new"]

        first = self.frm.recover_to(RecoveryTarget(tx_id=1))
        second = self.frm.recover_to(RecoveryTarget(tx_id=1))

        self.assertTrue(first.ok, first.message)
        self.assertTrue(second.ok, second.message)
        self.assertEqual(sm.rows.get(rid), [1, "old"])
        self.assertEqual(len(self._restore_positions()), 2)


class TestRestoreFailure(PITRTestCase):
    def test_tx_tidak_ditemukan_tidak_menulis_restore_atau_checkpoint(self) -> None:
        rid = RID("student", 0, 1)
        self._dependencies({rid: [1]})
        self._write(LogKind.BEGIN, 1)
        self._write(
            LogKind.DATA, 1,
            self._change(rid, ChangeOp.INSERT, NO_RECORD, [1]),
        )
        self._write(LogKind.COMMIT, 1)
        before = len(self.frm.read_log())

        result = self.frm.recover_to(RecoveryTarget(tx_id=99))

        self.assertFalse(result.ok)
        self.assertIn("tidak ditemukan", result.message)
        self.assertEqual(len(self.frm.read_log()), before)
        self.assertFalse(self._restore_positions())

    def test_recover_to_tanpa_dependensi_gagal(self) -> None:
        result = self.frm.recover_to(RecoveryTarget(tx_id=1))

        self.assertFalse(result.ok)
        self.assertIn("dependensi", result.message.lower())

    def test_restore_wal_kosong_tidak_menghapus_data_luar_wal(self) -> None:
        rid = RID("student", 0, 1)
        sm, _ = self._dependencies({rid: [9]})

        result = self.frm.recover_to(RecoveryTarget(timestamp=time.time()))

        self.assertTrue(result.ok, result.message)
        self.assertEqual(sm.rows, {rid: [9]})
        self.assertEqual(len(self._restore_positions()), 1)
