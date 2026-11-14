import tempfile
import unittest
from pathlib import Path

from common.errors import LogWriteError
from common.log import LogKind, LogResult
from common.storage import RID
from common.transaction import (
    NO_RECORD,
    AccessStatus,
    Action,
    ChangeOp,
    DataChange,
    ResourceId,
    TxResult,
    TxStatus,
)
from concurrency_control.frm_bridge import FrmBridge
from concurrency_control.manager import CCManager
from failure_recovery import FailureRecoveryManager

RID1 = RID("student", 0, 1)
ROW = ResourceId("student", RID1)


class _FakeFrm:
    def __init__(self) -> None:
        self.records = []
        self.flushed: list[int] = []
        self.fail_write = False
        self.fail_flush = False
        self.rolled_back: list[int] = []

    def rollback(self, tx_id: int) -> TxResult:
        self.rolled_back.append(tx_id)
        return TxResult(tx_id=tx_id, status=TxStatus.ABORTED, ok=True)

    def write_log(self, record):
        if self.fail_write:
            return LogResult(ok=False, message="disk penuh")
        self.records.append(record)
        return LogResult(ok=True, lsn=len(self.records))

    def flush_log(self, up_to_lsn: int) -> bool:
        self.flushed.append(up_to_lsn)
        return not self.fail_flush

    def rollback(self, tx_id: int) -> TxResult:
        return TxResult(tx_id, TxStatus.ABORTED, ok=True)


class FrmBridgeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.frm = _FakeFrm()
        self.bridge = FrmBridge(self.frm)

    def test_events_logged_with_tx_id(self) -> None:
        self.assertEqual(self.bridge.log_begin(7), 1)
        self.assertEqual(self.bridge.log_abort_request(7), 2)
        self.assertEqual(self.bridge.log_abort_end(7), 3)
        self.assertEqual(
            [(r.kind, r.tx_id) for r in self.frm.records],
            [(LogKind.BEGIN, 7), (LogKind.ABORT_REQUEST, 7), (LogKind.ABORT, 7)],
        )

    def test_write_logs_single_data_record(self) -> None:
        lsn = self.bridge.log_write(7, ChangeOp.UPDATE, "student", RID1, [1, "a"], [1, "b"])
        self.assertEqual(lsn, 1)
        (record,) = self.frm.records
        self.assertIs(record.kind, LogKind.DATA)
        self.assertEqual(record.tx_id, 7)
        self.assertEqual(
            record.change, DataChange("student", RID1, ChangeOp.UPDATE, [1, "a"], [1, "b"])
        )

    def test_commit_flushes_commit_record(self) -> None:
        self.bridge.log_begin(7)
        lsn = self.bridge.log_commit(7)
        self.assertIs(self.frm.records[-1].kind, LogKind.COMMIT)
        self.assertEqual(self.frm.flushed, [lsn])

    def test_abort_end_flushes(self) -> None:
        lsn = self.bridge.log_abort_end(7)
        self.assertEqual(self.frm.flushed, [lsn])

    def test_begin_and_write_do_not_flush(self) -> None:
        self.bridge.log_begin(7)
        self.bridge.log_write(7, ChangeOp.INSERT, "student", RID1, NO_RECORD, [1])
        self.assertEqual(self.frm.flushed, [])

    def test_write_failure_raises(self) -> None:
        self.frm.fail_write = True
        with self.assertRaises(LogWriteError):
            self.bridge.log_begin(7)
        with self.assertRaises(LogWriteError):
            self.bridge.log_commit(7)
        self.assertEqual(self.frm.flushed, [])

    def test_flush_failure_raises(self) -> None:
        self.frm.fail_flush = True
        with self.assertRaises(LogWriteError):
            self.bridge.log_commit(7)


class CcmWithFakeFrmTest(unittest.TestCase):
    def setUp(self) -> None:
        self.frm = _FakeFrm()
        self.ccm = CCManager(self.frm)

    @staticmethod
    def change() -> DataChange:
        return DataChange("student", RID1, ChangeOp.UPDATE, [1, "a"], [1, "b"])

    def test_read_writes_no_log(self) -> None:
        t1 = self.ccm.begin_transaction()
        self.ccm.validate_object(t1, ROW, Action.READ)
        self.assertEqual([r.kind for r in self.frm.records], [LogKind.BEGIN])

    def test_wait_retry_logs_once(self) -> None:
        t1, t2 = self.ccm.begin_transaction(), self.ccm.begin_transaction()
        self.ccm.validate_object(t1, ROW, Action.WRITE, self.change())
        for _ in range(3):
            resp = self.ccm.validate_object(t2, ROW, Action.WRITE, self.change())
            self.assertIs(resp.status, AccessStatus.WAIT)
        data = [r for r in self.frm.records if r.kind is LogKind.DATA]
        self.assertEqual(len(data), 1)

    def test_write_log_failure_aborts_access(self) -> None:
        t1 = self.ccm.begin_transaction()
        self.frm.fail_write = True
        resp = self.ccm.validate_object(t1, ROW, Action.WRITE, self.change())
        self.assertIs(resp.status, AccessStatus.ABORTED)
        self.assertIsNone(resp.lsn)
        self.assertIn("pencatatan log gagal", resp.message)

    def test_commit_flush_failure_not_committed(self) -> None:
        t1 = self.ccm.begin_transaction()
        self.frm.fail_flush = True
        result = self.ccm.commit(t1)
        self.assertFalse(result.ok)
        self.assertIn(t1, self.ccm._transactions)


class CcmWithRealFrmTest(unittest.TestCase):
    def test_begin_write_commit_in_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            frm = FailureRecoveryManager(log_path=str(Path(tmp) / "wal.log"))
            try:
                ccm = CCManager(frm)
                t1 = ccm.begin_transaction()
                change = DataChange("student", RID1, ChangeOp.UPDATE, [1, "a"], [1, "b"])
                resp = ccm.validate_object(t1, ROW, Action.WRITE, change)
                ccm.validate_object(t1, ROW, Action.READ)
                self.assertTrue(ccm.commit(t1).ok)

                log = frm.read_log(t1)
                self.assertEqual(
                    [r.kind for r in log], [LogKind.BEGIN, LogKind.DATA, LogKind.COMMIT]
                )
                self.assertEqual(log[1].lsn, resp.lsn)
                self.assertEqual(log[1].change, change)
            finally:
                frm.close()


if __name__ == "__main__":
    unittest.main()
