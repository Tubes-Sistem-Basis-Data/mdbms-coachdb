"""Uji point-in-time recovery dengan Storage Manager dan CCM nyata."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
from math import inf, nextafter
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import DEFAULT, patch

from common.catalog import ColumnSchema, DataType, IndexInfo, IndexKind
from common.errors import StorageError
from common.log import LogKind, LogRecord, RecoveryTarget
from common.storage import RID
from common.transaction import AccessStatus, Action, ChangeOp, DataChange, NO_RECORD, ResourceId
from concurrency_control import CCManager
from failure_recovery import FailureRecoveryManager
from main import bootstrap, close_runtime, parse_args
from storage_manager.facade import StorageManager


class PITRTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)
        self.sm = StorageManager(str(self.data_dir))
        self.sm._catalog.create_table(
            "student",
            (
                ColumnSchema("id", DataType.INTEGER, nullable=False),
                ColumnSchema("name", DataType.VARCHAR, length=50),
            ),
            ("id",),
            (),
        )
        self.sm.create_index(IndexInfo("idx_student_id", "student", "id", IndexKind.BPLUS_TREE))
        self.frm = FailureRecoveryManager(log_path=str(self.data_dir / "wal.log"))
        self.ccm = CCManager(self.frm)
        self.frm.set_dependencies(self.sm, self.ccm)
        self.sm.set_log_flusher(self.frm.flush_log)

    def tearDown(self) -> None:
        self.sm.close()
        self.frm.close()
        self._tmp.cleanup()

    def _insert(self, values: list) -> tuple[int, RID]:
        tx_id = self.ccm.begin_transaction()
        rid = self.sm.allocate_rid("student")
        change = DataChange("student", rid, ChangeOp.INSERT, NO_RECORD, values)
        access = self.ccm.validate_object(
            tx_id, ResourceId("student", rid), Action.WRITE, change
        )
        self.assertIs(access.status, AccessStatus.ALLOWED, access.message)
        self.sm.insert("student", rid, values, access.lsn)
        result = self.ccm.commit(tx_id)
        self.assertTrue(result.ok, result.message)
        return tx_id, rid

    def _update(self, rid: RID, values: list, *, abort: bool = False) -> int:
        tx_id = self.ccm.begin_transaction()
        before = self.sm.fetch("student", rid)
        self.assertIsNotNone(before)
        change = DataChange("student", rid, ChangeOp.UPDATE, before.values, values)
        access = self.ccm.validate_object(
            tx_id, ResourceId("student", rid), Action.WRITE, change
        )
        self.assertIs(access.status, AccessStatus.ALLOWED, access.message)
        self.sm.update("student", rid, values, access.lsn)
        result = self.ccm.abort(tx_id) if abort else self.ccm.commit(tx_id)
        self.assertTrue(result.ok, result.message)
        return tx_id

    def _delete(self, rid: RID) -> int:
        tx_id = self.ccm.begin_transaction()
        before = self.sm.fetch("student", rid)
        self.assertIsNotNone(before)
        change = DataChange("student", rid, ChangeOp.DELETE, before.values, NO_RECORD)
        access = self.ccm.validate_object(
            tx_id, ResourceId("student", rid), Action.WRITE, change
        )
        self.assertIs(access.status, AccessStatus.ALLOWED, access.message)
        self.sm.delete("student", rid, access.lsn)
        result = self.ccm.commit(tx_id)
        self.assertTrue(result.ok, result.message)
        return tx_id

class TestPITR(PITRTestCase):
    def test_restore_preserves_committed_update_after_aborted_update(self) -> None:
        _, rid = self._insert([1, "Awal"])
        self._update(rid, [1, "Batal"], abort=True)
        target_tx = self._update(rid, [1, "Tersimpan"])
        self._update(rid, [1, "Sesudah Target"])

        result = self.frm.recover_to(RecoveryTarget(tx_id=target_tx))

        self.assertTrue(result.ok, result.message)
        self.assertEqual([row.values for row in self.sm.scan("student")], [[1, "Tersimpan"]])

    def test_restore_to_tx_uses_real_sm_and_rebuilds_index(self) -> None:
        tx1, rid = self._insert([1, "Ani"])
        self.assertEqual(tx1, 1)
        checkpoint = self.frm.checkpoint()
        self.assertTrue(checkpoint.ok, checkpoint.message)

        tx2 = self._update(rid, [1, "Budi"])
        self.assertEqual(tx2, 2)
        self.assertEqual([row.values for row in self.sm.scan("student")], [[1, "Budi"]])

        result = self.frm.recover_to(RecoveryTarget(tx_id=1))

        self.assertTrue(result.ok, result.message)
        self.assertEqual([row.values for row in self.sm.scan("student")], [[1, "Ani"]])
        self.assertEqual(self.sm.index_lookup("student", "id", 1), [rid])
        records = self.frm.read_log()
        restore_positions = [
            index for index, record in enumerate(records)
            if record.kind is LogKind.RESTORE
        ]
        self.assertTrue(restore_positions)
        self.assertTrue(any(
            record.kind is LogKind.CHECKPOINT_END
            for record in records[restore_positions[-1] + 1:]
        ))

    def test_restore_to_time_undoes_real_update(self) -> None:
        _, rid = self._insert([1, "Ani"])
        first_commit = self.frm.read_log(1)[-1]
        time.sleep(0.01)
        self._update(rid, [1, "Budi"])
        second_data = next(
            record for record in self.frm.read_log(2)
            if record.kind is LogKind.DATA
        )
        target = (first_commit.timestamp + second_data.timestamp) / 2

        result = self.frm.recover_to(RecoveryTarget(timestamp=target))

        self.assertTrue(result.ok, result.message)
        self.assertEqual([row.values for row in self.sm.scan("student")], [[1, "Ani"]])

    def test_restore_to_time_undoes_uncommitted_real_transaction(self) -> None:
        _, rid = self._insert([1, "Ani"])
        before = self.sm.fetch("student", rid)
        self.assertIsNotNone(before)
        change = DataChange("student", rid, ChangeOp.UPDATE, before.values, [1, "Budi"])
        begin = self.frm.write_log(LogRecord(LogKind.BEGIN, tx_id=2))
        self.assertTrue(begin.ok, begin.message)
        data = self.frm.write_log(
            LogRecord(LogKind.DATA, tx_id=2, change=change)
        )
        self.assertTrue(data.ok, data.message)
        self.sm.update("student", rid, [1, "Budi"], data.lsn)

        result = self.frm.recover_to(RecoveryTarget(timestamp=time.time()))

        self.assertTrue(result.ok, result.message)
        self.assertEqual([row.values for row in self.sm.scan("student")], [[1, "Ani"]])

    def test_restore_to_tx_undoes_real_delete_and_insert(self) -> None:
        _, first_rid = self._insert([1, "Ani"])
        self._delete(first_rid)
        _, second_rid = self._insert([2, "Budi"])

        result = self.frm.recover_to(RecoveryTarget(tx_id=1))

        self.assertTrue(result.ok, result.message)
        self.assertEqual(
            [(row.rid, row.values) for row in self.sm.scan("student")],
            [(first_rid, [1, "Ani"])],
        )
        self.assertIsNone(self.sm.fetch("student", second_rid))

    def test_restore_to_tx_repeated_remains_consistent(self) -> None:
        _, rid = self._insert([1, "Ani"])
        self._update(rid, [1, "Budi"])

        first = self.frm.recover_to(RecoveryTarget(tx_id=1))
        second = self.frm.recover_to(RecoveryTarget(tx_id=1))

        self.assertTrue(first.ok, first.message)
        self.assertTrue(second.ok, second.message)
        self.assertEqual([row.values for row in self.sm.scan("student")], [[1, "Ani"]])
        self.assertEqual(
            sum(record.kind is LogKind.RESTORE for record in self.frm.read_log()), 2
        )

    def test_restore_to_later_transaction_replays_committed_wal(self) -> None:
        tx1, rid = self._insert([1, "Awal"])
        self.assertTrue(self.frm.checkpoint().ok)
        tx2 = self._update(rid, [1, "Versi2"])
        self.assertTrue(self.frm.checkpoint().ok)

        first = self.frm.recover_to(RecoveryTarget(tx_id=tx1))
        self.assertTrue(first.ok, first.message)
        self.assertEqual([row.values for row in self.sm.scan("student")], [[1, "Awal"]])

        second = self.frm.recover_to(RecoveryTarget(tx_id=tx2))
        self.assertTrue(second.ok, second.message)
        self.assertEqual([row.values for row in self.sm.scan("student")], [[1, "Versi2"]])
        self.assertEqual(self.sm.index_lookup("student", "id", 1), [rid])

    def test_failed_replay_preserves_live_data(self) -> None:
        tx1, rid = self._insert([1, "Awal"])
        self.assertTrue(self.frm.checkpoint().ok)
        tx2 = self._update(rid, [1, "Versi2"])
        self.assertTrue(self.frm.recover_to(RecoveryTarget(tx_id=tx1)).ok)
        self._update(rid, [1, "Data Aktif"])
        records = self.frm.read_log()

        with patch.object(self.sm, "update", side_effect=StorageError("replay gagal")):
            result = self.frm.recover_to(RecoveryTarget(tx_id=tx2))

        self.assertFalse(result.ok, result.message)
        self.assertIn("replay gagal", result.message)
        self.assertEqual([row.values for row in self.sm.scan("student")], [[1, "Data Aktif"]])
        self.assertEqual(self.frm.read_log(), records)

    def test_time_outside_history_preserves_wal_and_data(self) -> None:
        self._insert([1, "Awal"])
        records = self.frm.read_log()
        before_history = records[0].timestamp - 1
        future = time.time() + 60

        for timestamp in (before_history, future):
            with self.subTest(timestamp=timestamp):
                result = self.frm.recover_to(RecoveryTarget(timestamp=timestamp))

                self.assertFalse(result.ok, result.message)
                self.assertIn("di luar rentang", result.message)
                self.assertEqual([row.values for row in self.sm.scan("student")], [[1, "Awal"]])
                self.assertEqual(self.frm.read_log(), records)

    def test_restore_includes_commit_at_submicrosecond_target(self) -> None:
        start = float(int(time.time()) - 10)
        target = nextafter(start + 1, inf)
        with patch("time.time", return_value=start):
            _, rid = self._insert([1, "Awal"])
        with patch("time.time", return_value=target):
            self._update(rid, [1, "TepatBatas"])
        with patch("time.time", return_value=start + 2):
            self._update(rid, [1, "SesudahBatas"])

        result = self.frm.recover_to(RecoveryTarget(timestamp=target))

        self.assertTrue(result.ok, result.message)
        self.assertEqual([row.values for row in self.sm.scan("student")], [[1, "TepatBatas"]])

    def test_failed_finalization_restores_data_and_can_retry(self) -> None:
        tx_id, rid = self._insert([1, "Awal"])
        self.assertTrue(self.frm.checkpoint().ok)
        self._update(rid, [1, "VersiAktif"])
        store = self.frm._checkpoint_store
        records = self.frm.read_log()
        master = store.read_master()
        snapshots = set(Path(store.snapshot_dir).iterdir())

        failures = (
            patch.object(self.sm, "rebuild_indexes", wraps=self.sm.rebuild_indexes,
                         side_effect=[StorageError("gagal rebuild index"), DEFAULT]),
            patch.object(store, "write_master_atomic", side_effect=StorageError("gagal master")),
        )
        for failure in failures:
            with self.subTest(method=failure.attribute), failure:
                result = self.frm.recover_to(RecoveryTarget(tx_id=tx_id))
                self.assertFalse(result.ok, result.message)
                self.assertIn("gagal", result.message)
                self.assertEqual([row.values for row in self.sm.scan("student")], [[1, "VersiAktif"]])
                self.assertEqual(self.sm.index_lookup("student", "id", 1), [rid])
                self.assertEqual(self.frm.read_log(), records)
                self.assertEqual(store.read_master(), master)
                self.assertEqual(set(Path(store.snapshot_dir).iterdir()), snapshots)

        result = self.frm.recover_to(RecoveryTarget(tx_id=tx_id))
        self.assertTrue(result.ok, result.message)
        self.assertEqual([row.values for row in self.sm.scan("student")], [[1, "Awal"]])
        tail = self.frm.read_log()[len(records):]
        self.assertEqual([record.lsn for record in tail], list(range(records[-1].lsn + 1, records[-1].lsn + 4)))
        self.assertEqual([record.kind for record in tail], [
            LogKind.RESTORE, LogKind.CHECKPOINT_BEGIN, LogKind.CHECKPOINT_END,
        ])

    def test_unknown_tx_does_not_write_restore_or_checkpoint(self) -> None:
        self._insert([1, "Ani"])
        before = len(self.frm.read_log())

        result = self.frm.recover_to(RecoveryTarget(tx_id=99))

        self.assertFalse(result.ok)
        self.assertIn("tidak ditemukan", result.message)
        self.assertEqual(len(self.frm.read_log()), before)
        self.assertFalse(any(
            record.kind is LogKind.RESTORE for record in self.frm.read_log()
        ))

    def test_empty_wal_preserves_real_data_outside_wal(self) -> None:
        self.sm.bulk_load("student", [[9, "Luar WAL"]])

        result = self.frm.recover_to(RecoveryTarget(timestamp=time.time()))

        self.assertTrue(result.ok, result.message)
        self.assertEqual(
            [row.values for row in self.sm.scan("student")], [[9, "Luar WAL"]]
        )
        self.assertEqual(
            sum(record.kind is LogKind.RESTORE for record in self.frm.read_log()), 1
        )


class TestPITRDependencies(unittest.TestCase):
    def test_recover_to_without_dependencies_reports_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            frm = FailureRecoveryManager(log_path=str(Path(directory) / "wal.log"))
            try:
                result = frm.recover_to(RecoveryTarget(tx_id=1))
                self.assertFalse(result.ok)
                self.assertIn("dependensi", result.message.lower())
            finally:
                frm.close()


class TestPITRQueryPath(unittest.TestCase):
    def test_restore_to_tx_through_qp_for_both_protocols(self) -> None:
        for protocol in ("lock", "timestamp"):
            with self.subTest(protocol=protocol):
                with tempfile.TemporaryDirectory() as directory:
                    sm = StorageManager(directory)
                    try:
                        sm._catalog.create_table(
                            "student",
                            (
                                ColumnSchema("id", DataType.INTEGER, nullable=False),
                                ColumnSchema("name", DataType.VARCHAR, length=50),
                            ),
                            ("id",),
                            (),
                        )
                        sm.create_index(
                            IndexInfo("idx_student_id", "student", "id", IndexKind.BPLUS_TREE)
                        )
                    finally:
                        sm.close()

                    with redirect_stdout(io.StringIO()):
                        qp = bootstrap(parse_args([
                            "--data-dir", directory,
                            "--protocol", protocol,
                        ]))
                    session = qp.open_session()
                    try:
                        for sql in (
                            "BEGIN;",
                            "INSERT INTO student VALUES (1, 'Ani');",
                            "COMMIT;",
                            "CHECKPOINT;",
                            "BEGIN;",
                            "UPDATE student SET name = 'Budi' WHERE id = 1;",
                            "COMMIT;",
                        ):
                            result = qp.execute_query(sql, session)
                            self.assertTrue(result.ok, f"{sql}: {result.message}")

                        restore = qp.execute_query("RESTORE TO TX 1;", session)
                        self.assertTrue(restore.ok, restore.message)
                        selected = qp.execute_query("SELECT * FROM student;", session)
                        self.assertTrue(selected.ok, selected.message)
                        self.assertEqual(selected.columns, ["id", "name"])
                        self.assertEqual(selected.rows, [[1, "Ani"]])
                    finally:
                        qp.close_session(session.session_id)
                        close_runtime(qp)
