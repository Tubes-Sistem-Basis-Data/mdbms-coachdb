import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path

from common.errors import MdbmsError
from common.log import LogKind
from common.storage import RID
from common.transaction import AccessStatus, Action, ChangeOp, DataChange, ResourceId, TxResult, TxStatus
from concurrency_control.driver import _MemoryFrm
from concurrency_control.manager import CCManager
from failure_recovery import FailureRecoveryManager


A = ResourceId("A", RID("A", 0, 0))
B = ResourceId("B", RID("B", 0, 0))


def write(ccm: CCManager, tx_id: int, resource: ResourceId) -> AccessStatus:
    change = DataChange(resource.table, resource.rid, ChangeOp.UPDATE, [0], [tx_id])
    return ccm.validate_object(tx_id, resource, Action.WRITE, change).status


class DeadlockRollbackTest(unittest.TestCase):
    def test_victim_retry_undoes_write_before_releasing_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as cleanup:
            frm = FailureRecoveryManager(log_path=str(Path(tmp) / "wal.log"))
            cleanup.callback(frm.close)
            ccm = CCManager(frm)
            t1, t2 = ccm.begin_transaction(), ccm.begin_transaction()
            during_undo: list[AccessStatus] = []

            class Storage:
                rows = {B.rid: [t2]}

                def update(self, table: str, rid: RID, values: list, lsn: int | None) -> None:
                    during_undo.append(ccm.validate_object(t1, B, Action.READ).status)
                    self.rows[rid] = list(values)

            sm = Storage()
            frm.set_dependencies(sm, ccm)
            self.assertIs(write(ccm, t1, A), AccessStatus.ALLOWED)
            self.assertIs(write(ccm, t2, B), AccessStatus.ALLOWED)
            self.assertIs(write(ccm, t2, A), AccessStatus.WAIT)
            # T1 menutup siklus, tapi T2 baru menerima ABORTED saat retry.
            self.assertIs(ccm.validate_object(t1, B, Action.READ).status, AccessStatus.WAIT)
            self.assertIs(write(ccm, t2, A), AccessStatus.ABORTED)

            self.assertEqual(during_undo, [AccessStatus.WAIT])
            self.assertEqual(sm.rows[B.rid], [0])
            self.assertIs(ccm.validate_object(t1, B, Action.READ).status, AccessStatus.ALLOWED)
            self.assertTrue(ccm.commit(t1).ok)
            self.assertFalse(ccm.commit(t2).ok)
            self.assertEqual(
                [record.kind for record in frm.read_log(t2)],
                [LogKind.BEGIN, LogKind.DATA, LogKind.ABORT_REQUEST, LogKind.ABORT],
            )

    def test_selected_victim_cannot_commit_instead_of_retrying(self) -> None:
        frm = _MemoryFrm()
        ccm = CCManager(frm)
        t1, t2 = ccm.begin_transaction(), ccm.begin_transaction()
        self.assertIs(write(ccm, t1, A), AccessStatus.ALLOWED)
        self.assertIs(write(ccm, t2, B), AccessStatus.ALLOWED)
        self.assertIs(write(ccm, t2, A), AccessStatus.WAIT)
        self.assertIs(write(ccm, t1, B), AccessStatus.WAIT)

        result = ccm.commit(t2)

        self.assertFalse(result.ok)
        self.assertIs(result.status, TxStatus.ABORTED)
        self.assertIs(write(ccm, t1, B), AccessStatus.ALLOWED)
        self.assertTrue(ccm.commit(t1).ok)
        self.assertEqual(
            [record.kind for record in frm.records if record.tx_id == t2],
            [LogKind.BEGIN, LogKind.DATA, LogKind.ABORT_REQUEST, LogKind.ABORT],
        )

    def test_failed_rollback_keeps_lock_until_retry_succeeds(self) -> None:
        class FailingFrm(_MemoryFrm):
            fail_rollback = True

            def rollback(self, tx_id: int) -> TxResult:
                if self.fail_rollback:
                    return TxResult(tx_id, TxStatus.FAILED, ok=False, message="undo gagal")
                return super().rollback(tx_id)

        frm = FailingFrm()
        ccm = CCManager(frm)
        t1, t2 = ccm.begin_transaction(), ccm.begin_transaction()
        self.assertIs(write(ccm, t1, A), AccessStatus.ALLOWED)
        self.assertIs(write(ccm, t2, B), AccessStatus.ALLOWED)
        self.assertIs(write(ccm, t1, B), AccessStatus.WAIT)

        with self.assertRaisesRegex(MdbmsError, "Rollback gagal: undo gagal"):
            write(ccm, t2, A)
        with self.assertRaisesRegex(MdbmsError, f"Rollback T{t2} belum selesai"):
            write(ccm, t2, A)

        self.assertIs(write(ccm, t1, B), AccessStatus.WAIT)
        self.assertFalse(ccm.commit(t2).ok)
        frm.fail_rollback = False
        self.assertTrue(ccm.abort(t2).ok)
        self.assertIs(write(ccm, t1, B), AccessStatus.ALLOWED)
        self.assertTrue(ccm.commit(t1).ok)
