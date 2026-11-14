import unittest

from common.storage import RID
from common.transaction import Action, ResourceId
from concurrency_control.serializability import ScheduleRecorder, SerializabilityChecker

ROW_A = ResourceId("A", RID("A", 0, 0))
ROW_B = ResourceId("A", RID("A", 0, 1))
TABLE = ResourceId("A", None)
R, W = Action.READ, Action.WRITE


def check(ops, finishes):
    recorder = ScheduleRecorder()
    for tx_id, resource, action in ops:
        recorder.record(tx_id, resource, action)
    for tx_id, kind in finishes:
        recorder.record_finish(tx_id, kind)
    return SerializabilityChecker().check(recorder.committed_schedule())


class SerializabilityCheckerTest(unittest.TestCase):
    def test_conflict_without_cycle_gives_serial_order(self) -> None:
        result = check([(1, ROW_A, W), (2, ROW_A, R)], [(1, "COMMIT"), (2, "COMMIT")])
        self.assertTrue(result.serializable)
        self.assertEqual(result.serial_order, [1, 2])

    def test_cycle_is_not_serializable(self) -> None:
        ops = [(1, ROW_A, R), (2, ROW_A, W), (2, ROW_B, W), (1, ROW_B, R)]
        result = check(ops, [(1, "COMMIT"), (2, "COMMIT")])
        self.assertFalse(result.serializable)
        self.assertEqual(set(result.cycle), {1, 2})

    def test_aborted_transaction_is_ignored(self) -> None:
        ops = [(1, ROW_A, R), (2, ROW_A, W), (2, ROW_B, W), (1, ROW_B, R)]
        result = check(ops, [(1, "COMMIT"), (2, "ABORT")])
        self.assertTrue(result.serializable)
        self.assertEqual(result.serial_order, [1])

    def test_reads_only_do_not_conflict(self) -> None:
        result = check([(1, ROW_A, R), (2, ROW_A, R)], [(1, "COMMIT"), (2, "COMMIT")])
        self.assertTrue(result.serializable)

    def test_table_access_conflicts_with_row_access(self) -> None:
        ops = [(1, TABLE, R), (2, ROW_A, W), (2, ROW_B, R), (1, ROW_B, W)]
        result = check(ops, [(1, "COMMIT"), (2, "COMMIT")])
        self.assertFalse(result.serializable)


if __name__ == "__main__":
    unittest.main()
