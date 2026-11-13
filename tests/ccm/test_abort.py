import unittest

from common.log import LogKind, LogResult
from common.storage import RID
from common.transaction import (
    AccessStatus,
    Action,
    ChangeOp,
    DataChange,
    ProtocolType,
    ResourceId,
    TxResult,
    TxStatus,
)
from concurrency_control.manager import CCManager

RID1 = RID("student", 0, 1)
ROW = ResourceId("student", RID1)


class _FakeFrm:
    def __init__(self) -> None:
        self.events: list[tuple[str, int]] = []
        self.fail_request = False
        self.rollback_ok = True
        self.ccm: CCManager | None = None
        self.locked_during_rollback: bool | None = None

    def write_log(self, record):
        if record.kind is LogKind.ABORT_REQUEST and self.fail_request:
            return LogResult(ok=False, message="disk penuh")
        self.events.append((record.kind.value, record.tx_id))
        return LogResult(ok=True, lsn=len(self.events))

    def flush_log(self, up_to_lsn: int) -> bool:
        return True

    def rollback(self, tx_id: int) -> TxResult:
        self.events.append(("ROLLBACK", tx_id))
        if self.ccm is not None:
            self.locked_during_rollback = bool(self.ccm._protocol.held_locks(tx_id))
        if not self.rollback_ok:
            return TxResult(tx_id=tx_id, status=TxStatus.FAILED, ok=False, message="undo gagal")
        return TxResult(tx_id=tx_id, status=TxStatus.ABORTED, ok=True)


def change() -> DataChange:
    return DataChange("student", RID1, ChangeOp.UPDATE, [1, "a"], [1, "b"])


class AbortFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.frm = _FakeFrm()
        self.ccm = CCManager(self.frm)
        self.frm.ccm = self.ccm

    def test_abort_logs_request_then_rolls_back(self) -> None:
        t1 = self.ccm.begin_transaction()
        self.frm.events.clear()
        result = self.ccm.abort(t1)
        self.assertTrue(result.ok)
        self.assertIs(result.status, TxStatus.ABORTED)
        self.assertEqual(self.frm.events, [("ABORT_REQUEST", t1), ("ROLLBACK", t1)])
        self.assertNotIn(t1, self.ccm._transactions)

    def test_locks_held_during_rollback_and_released_after(self) -> None:
        t1, t2 = self.ccm.begin_transaction(), self.ccm.begin_transaction()
        self.ccm.validate_object(t1, ROW, Action.WRITE, change())
        self.assertIs(self.ccm.validate_object(t2, ROW, Action.READ).status, AccessStatus.WAIT)

        self.ccm.abort(t1)

        self.assertTrue(self.frm.locked_during_rollback)
        self.assertIs(self.ccm.validate_object(t2, ROW, Action.READ).status, AccessStatus.ALLOWED)

    def test_rollback_failure_keeps_locks_and_allows_retry(self) -> None:
        t1, t2 = self.ccm.begin_transaction(), self.ccm.begin_transaction()
        self.ccm.validate_object(t1, ROW, Action.WRITE, change())
        self.frm.rollback_ok = False

        result = self.ccm.abort(t1)

        self.assertFalse(result.ok)
        self.assertIn("undo gagal", result.message)
        self.assertIs(self.ccm._transactions[t1].status, TxStatus.FAILED)
        self.assertIs(self.ccm.validate_object(t2, ROW, Action.READ).status, AccessStatus.WAIT)

        self.frm.rollback_ok = True
        self.assertTrue(self.ccm.abort(t1).ok)
        self.assertIs(self.ccm.validate_object(t2, ROW, Action.READ).status, AccessStatus.ALLOWED)

    def test_abort_request_log_failure_keeps_transaction(self) -> None:
        t1 = self.ccm.begin_transaction()
        self.frm.fail_request = True
        result = self.ccm.abort(t1)
        self.assertFalse(result.ok)
        self.assertNotIn(("ROLLBACK", t1), self.frm.events)
        self.assertIn(t1, self.ccm._transactions)

    def test_abort_is_idempotent(self) -> None:
        t1 = self.ccm.begin_transaction()
        self.ccm.abort(t1)
        self.frm.events.clear()
        again = self.ccm.abort(t1)
        self.assertTrue(again.ok)
        self.assertEqual(self.frm.events, [])

    def test_abort_unknown_transaction_fails(self) -> None:
        result = self.ccm.abort(99)
        self.assertFalse(result.ok)
        self.assertEqual(self.frm.events, [])

    def test_commit_after_abort_rejected(self) -> None:
        t1 = self.ccm.begin_transaction()
        self.ccm.abort(t1)
        result = self.ccm.commit(t1)
        self.assertFalse(result.ok)
        self.assertIs(result.status, TxStatus.ABORTED)
        self.assertIn("dibatalkan", result.message)
        self.assertNotIn(("COMMIT", t1), self.frm.events)

    def test_access_after_abort_rejected_with_message(self) -> None:
        t1 = self.ccm.begin_transaction()
        self.ccm.abort(t1)
        resp = self.ccm.validate_object(t1, ROW, Action.READ)
        self.assertIs(resp.status, AccessStatus.ABORTED)
        self.assertIn("dibatalkan", resp.message)

    def test_abort_after_commit_rejected(self) -> None:
        t1 = self.ccm.begin_transaction()
        self.ccm.commit(t1)
        self.frm.events.clear()
        self.assertFalse(self.ccm.abort(t1).ok)
        self.assertEqual(self.frm.events, [])

    def test_failed_commit_cannot_be_aborted(self) -> None:
        t1 = self.ccm.begin_transaction()
        self.ccm._transactions[t1].status = TxStatus.FAILED
        self.assertFalse(self.ccm.abort(t1).ok)
        self.assertNotIn(("ROLLBACK", t1), self.frm.events)


class DeadlockAbortTest(unittest.TestCase):
    def setUp(self) -> None:
        self.frm = _FakeFrm()
        self.ccm = CCManager(self.frm)
        self.frm.ccm = self.ccm

    def _make_deadlock(self) -> tuple[int, int]:
        r1, r2 = ResourceId("student", RID("student", 0, 1)), ResourceId("student", RID("student", 0, 2))
        t1, t2 = self.ccm.begin_transaction(), self.ccm.begin_transaction()
        self.ccm.validate_object(t1, r1, Action.WRITE, change())
        self.ccm.validate_object(t2, r2, Action.WRITE, change())
        self.assertIs(self.ccm.validate_object(t1, r2, Action.WRITE, change()).status, AccessStatus.WAIT)
        resp = self.ccm.validate_object(t2, r1, Action.WRITE, change())
        self.assertIs(resp.status, AccessStatus.ABORTED)
        return t1, t2

    def test_deadlock_victim_goes_through_same_abort_flow(self) -> None:
        t1, t2 = self._make_deadlock()
        self.assertIn(("ABORT_REQUEST", t2), self.frm.events)
        self.assertIn(("ROLLBACK", t2), self.frm.events)
        self.assertNotIn(t2, self.ccm._transactions)
        self.assertIn(t1, self.ccm._transactions)

    def test_victim_locks_released_so_other_proceeds(self) -> None:
        t1, _ = self._make_deadlock()
        r2 = ResourceId("student", RID("student", 0, 2))
        resp = self.ccm.validate_object(t1, r2, Action.WRITE, change())
        self.assertIs(resp.status, AccessStatus.ALLOWED)

    def test_victim_request_afterwards_says_cancelled(self) -> None:
        _, t2 = self._make_deadlock()
        resp = self.ccm.validate_object(t2, ROW, Action.READ)
        self.assertIs(resp.status, AccessStatus.ABORTED)
        self.assertIn("dibatalkan", resp.message)

    def test_rollback_failure_reported_in_response(self) -> None:
        r1, r2 = ResourceId("student", RID("student", 0, 1)), ResourceId("student", RID("student", 0, 2))
        t1, t2 = self.ccm.begin_transaction(), self.ccm.begin_transaction()
        self.ccm.validate_object(t1, r1, Action.WRITE, change())
        self.ccm.validate_object(t2, r2, Action.WRITE, change())
        self.ccm.validate_object(t1, r2, Action.WRITE, change())
        self.frm.rollback_ok = False
        resp = self.ccm.validate_object(t2, r1, Action.WRITE, change())
        self.assertIs(resp.status, AccessStatus.ABORTED)
        self.assertIn("undo gagal", resp.message)
        self.assertIn(t2, self.ccm._transactions)


class TimestampAbortTest(unittest.TestCase):
    def test_protocol_abort_response_triggers_abort_flow(self) -> None:
        frm = _FakeFrm()
        ccm = CCManager(frm, ProtocolType.TIMESTAMP_BASED)
        t1 = ccm.begin_transaction()

        class _Rejecting:
            def request(self, tx, resource, action):
                from common.transaction import AccessResponse

                return AccessResponse(status=AccessStatus.ABORTED, message="pelanggaran timestamp")

            def on_finish(self, tx_id):
                return None

        ccm._protocol = _Rejecting()
        resp = ccm.validate_object(t1, ROW, Action.READ)
        self.assertIs(resp.status, AccessStatus.ABORTED)
        self.assertIn("pelanggaran timestamp", resp.message)
        self.assertIn(("ROLLBACK", t1), frm.events)
        self.assertNotIn(t1, ccm._transactions)


if __name__ == "__main__":
    unittest.main()
