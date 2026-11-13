import unittest

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
from concurrency_control.frm_bridge import AbortCoordinator
from concurrency_control.lock_manager import LockMode
from concurrency_control.manager import CCManager

ALLOWED, WAIT, ABORTED = AccessStatus.ALLOWED, AccessStatus.WAIT, AccessStatus.ABORTED
READ, WRITE = Action.READ, Action.WRITE
TABLE = ResourceId("student")


def row(slot: int, table: str = "student") -> ResourceId:
    return ResourceId(table, RID(table, 0, slot))


def update(slot: int) -> DataChange:
    return DataChange("student", RID("student", 0, slot), ChangeOp.UPDATE, [1, "a"], [1, "b"])


def insert(slot: int) -> DataChange:
    return DataChange("student", RID("student", 0, slot), ChangeOp.INSERT, NO_RECORD, [slot, "x"])


class _Bridge:
    def __init__(self, ccm: CCManager) -> None:
        self.ccm = ccm
        self.writes: list[tuple] = []

    def log_begin(self, tx_id: int) -> int:
        return 0

    def log_write(self, tx_id, op, table, rid, before, after) -> int:
        assert not self.ccm._mutex.locked()
        self.writes.append((tx_id, op, table, rid, before, after))
        return 100 + len(self.writes)

    def log_commit(self, tx_id: int) -> int:
        return 0

    def rollback(self, tx_id: int) -> TxResult:
        return TxResult(tx_id, TxStatus.ABORTED, ok=True)


class ValidateObjectTest(unittest.TestCase):
    def setUp(self) -> None:
        self.ccm = CCManager(frm=None)
        self.bridge = _Bridge(self.ccm)
        self.ccm._bridge = self.bridge
        self.ccm._aborts = AbortCoordinator(self.bridge)

    def validate(self, tx_id: int, resource: ResourceId, action: Action, change: DataChange | None = None):
        return self.ccm.validate_object(tx_id, resource, action, change)

    def test_read_allowed_without_lsn_or_log(self) -> None:
        t1 = self.ccm.begin_transaction()
        resp = self.validate(t1, row(1), READ)
        self.assertIs(resp.status, ALLOWED)
        self.assertIsNone(resp.lsn)
        self.assertEqual(self.bridge.writes, [])
        self.assertEqual(
            self.ccm._protocol.held_locks(t1),
            {TABLE: LockMode.IS, row(1): LockMode.S},
        )

    def test_write_allowed_returns_lsn_and_logs_once(self) -> None:
        t1 = self.ccm.begin_transaction()
        change = update(1)
        resp = self.validate(t1, row(1), WRITE, change)
        self.assertIs(resp.status, ALLOWED)
        self.assertEqual(resp.lsn, 101)
        self.assertEqual(
            self.bridge.writes,
            [(t1, ChangeOp.UPDATE, "student", change.rid, change.before, change.after)],
        )
        self.assertEqual(
            self.ccm._protocol.held_locks(t1),
            {TABLE: LockMode.IX, row(1): LockMode.X},
        )

    def test_write_without_change_rejected(self) -> None:
        t1 = self.ccm.begin_transaction()
        with self.assertRaises(ValueError):
            self.validate(t1, row(1), WRITE)
        self.assertEqual(self.ccm._protocol.held_locks(t1), {})

    def test_inactive_tx_aborted(self) -> None:
        t1 = self.ccm.begin_transaction()
        self.ccm.commit(t1)
        for target in (t1, 99):
            resp = self.validate(target, row(1), READ)
            self.assertIs(resp.status, ABORTED)
            self.assertIn("tidak aktif", resp.message)

    def test_conflict_waits_then_allowed_after_commit(self) -> None:
        t1, t2 = self.ccm.begin_transaction(), self.ccm.begin_transaction()
        self.assertIs(self.validate(t1, row(1), WRITE, update(1)).status, ALLOWED)

        for _ in range(3):
            self.assertIs(self.validate(t2, row(1), WRITE, update(1)).status, WAIT)
        self.assertEqual(len(self.bridge.writes), 1)

        self.ccm.commit(t1)
        resp = self.validate(t2, row(1), WRITE, update(1))
        self.assertIs(resp.status, ALLOWED)
        self.assertEqual(resp.lsn, 102)
        self.assertEqual(len(self.bridge.writes), 2)

    def test_shared_readers_do_not_wait(self) -> None:
        t1, t2 = self.ccm.begin_transaction(), self.ccm.begin_transaction()
        self.assertIs(self.validate(t1, row(1), READ).status, ALLOWED)
        self.assertIs(self.validate(t2, row(1), READ).status, ALLOWED)

    def test_table_scan_blocks_insert(self) -> None:
        t1, t2 = self.ccm.begin_transaction(), self.ccm.begin_transaction()
        self.assertIs(self.validate(t1, TABLE, READ).status, ALLOWED)
        self.assertIs(self.validate(t2, row(9), WRITE, insert(9)).status, WAIT)
        self.assertEqual(self.bridge.writes, [])
        self.ccm.commit(t1)
        self.assertIs(self.validate(t2, row(9), WRITE, insert(9)).status, ALLOWED)

    def test_ddl_style_table_write_takes_x(self) -> None:
        t1, t2 = self.ccm.begin_transaction(), self.ccm.begin_transaction()
        change = DataChange("student", RID("student", 0, 0), ChangeOp.DELETE, [1], NO_RECORD)
        self.assertIs(self.validate(t1, TABLE, WRITE, change).status, ALLOWED)
        self.assertEqual(self.ccm._protocol.held_locks(t1), {TABLE: LockMode.X})
        self.assertIs(self.validate(t2, row(1), READ).status, WAIT)

    def test_deadlock_reports_aborted_to_youngest(self) -> None:
        t1, t2 = self.ccm.begin_transaction(), self.ccm.begin_transaction()
        self.assertIs(self.validate(t1, row(1), WRITE, update(1)).status, ALLOWED)
        self.assertIs(self.validate(t2, row(2), WRITE, update(2)).status, ALLOWED)
        self.assertIs(self.validate(t1, row(2), WRITE, update(2)).status, WAIT)
        resp = self.validate(t2, row(1), WRITE, update(1))
        self.assertIs(resp.status, ABORTED)
        self.assertIn("deadlock", resp.message)
        self.assertEqual(len(self.bridge.writes), 2)

    def test_allowed_access_recorded_in_schedule(self) -> None:
        calls: list[tuple] = []
        self.ccm.schedule.record = lambda tx, res, act: calls.append((tx, res, act))
        t1, t2 = self.ccm.begin_transaction(), self.ccm.begin_transaction()
        self.validate(t1, row(1), WRITE, update(1))
        self.validate(t2, row(1), READ)
        self.assertEqual(calls, [(t1, row(1), WRITE)])


if __name__ == "__main__":
    unittest.main()
