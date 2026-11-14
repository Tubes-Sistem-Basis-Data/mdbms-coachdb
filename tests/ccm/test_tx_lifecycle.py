import threading
import unittest

from common.transaction import Action, ResourceId, TxStatus
from concurrency_control.manager import CCManager


class _Bridge:
    def __init__(self, ccm: CCManager, fail_commit: bool = False) -> None:
        self.ccm = ccm
        self.calls: list[tuple[str, int]] = []
        self.fail_commit = fail_commit

    def log_begin(self, tx_id: int) -> int:
        assert not self.ccm._mutex.locked()
        self.calls.append(("BEGIN", tx_id))
        return len(self.calls)

    def log_commit(self, tx_id: int) -> int:
        assert not self.ccm._mutex.locked()
        self.calls.append(("COMMIT", tx_id))
        if self.fail_commit:
            raise OSError("fsync gagal")
        return len(self.calls)


class TxLifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.ccm = CCManager(frm=None)
        self.bridge = _Bridge(self.ccm)
        self.ccm._bridge = self.bridge

    def test_begin_ids_increase_and_logged(self) -> None:
        t1, t2 = self.ccm.begin_transaction(), self.ccm.begin_transaction()
        self.assertEqual((t1, t2), (1, 2))
        self.assertEqual(self.bridge.calls, [("BEGIN", 1), ("BEGIN", 2)])

    def test_commit_logs_then_releases_locks(self) -> None:
        tx_id = self.ccm.begin_transaction()
        tx = self.ccm._transactions[tx_id]
        self.ccm._protocol.request(tx, ResourceId("student"), Action.WRITE)
        self.assertTrue(self.ccm._protocol.held_locks(tx_id))

        result = self.ccm.commit(tx_id)

        self.assertTrue(result.ok)
        self.assertIs(result.status, TxStatus.COMMITTED)
        self.assertIs(tx.status, TxStatus.COMMITTED)
        self.assertEqual(self.bridge.calls[-1], ("COMMIT", tx_id))
        self.assertEqual(self.ccm._protocol.held_locks(tx_id), {})
        self.assertNotIn(tx_id, self.ccm._transactions)

    def test_commit_inactive_tx_fails(self) -> None:
        tx_id = self.ccm.begin_transaction()
        self.ccm.commit(tx_id)
        for target in (tx_id, 99):
            result = self.ccm.commit(target)
            self.assertFalse(result.ok)
            self.assertIs(result.status, TxStatus.FAILED)

    def test_commit_log_failure_keeps_tx_and_locks(self) -> None:
        self.bridge.fail_commit = True
        tx_id = self.ccm.begin_transaction()
        tx = self.ccm._transactions[tx_id]
        self.ccm._protocol.request(tx, ResourceId("student"), Action.WRITE)

        result = self.ccm.commit(tx_id)

        self.assertFalse(result.ok)
        self.assertIn("tidak pasti", result.message)
        self.assertIs(tx.status, TxStatus.FAILED)
        self.assertIn(tx_id, self.ccm._transactions)
        self.assertTrue(self.ccm._protocol.held_locks(tx_id))

    def test_begin_blocks_while_paused(self) -> None:
        self.ccm.pause_new_transactions()
        got: list[int] = []
        worker = threading.Thread(target=lambda: got.append(self.ccm.begin_transaction()))
        worker.start()
        worker.join(0.1)
        self.assertTrue(worker.is_alive())
        self.assertEqual(got, [])

        self.ccm.resume_transactions()
        worker.join(1)
        self.assertEqual(got, [1])

    def test_wait_until_idle_returns_after_commit(self) -> None:
        tx_id = self.ccm.begin_transaction()
        done = threading.Event()
        waiter = threading.Thread(target=lambda: (self.ccm.wait_until_idle(), done.set()))
        waiter.start()
        self.assertFalse(done.wait(0.1))

        self.ccm.commit(tx_id)
        self.assertTrue(done.wait(1))
        waiter.join(1)

    def test_wait_until_idle_no_active_returns(self) -> None:
        self.ccm.wait_until_idle()

    def test_initialize_continues_tx_id_after_recovery(self) -> None:
        self.ccm.initialize(41)
        self.assertEqual(self.ccm.begin_transaction(), 42)

    def test_initialize_timestamp_exceeds_recovered_tx_id(self) -> None:
        self.ccm.initialize(41)
        tx_id = self.ccm.begin_transaction()
        self.assertGreater(self.ccm._transactions[tx_id].timestamp, 41)

    def test_initialize_zero_starts_from_one(self) -> None:
        self.ccm.initialize(0)
        self.assertEqual(self.ccm.begin_transaction(), 1)

    def test_initialize_never_lowers_counter(self) -> None:
        self.ccm.begin_transaction()
        self.ccm.begin_transaction()
        self.ccm.initialize(1)
        self.assertEqual(self.ccm.begin_transaction(), 3)

    def test_initialize_rejects_negative(self) -> None:
        with self.assertRaises(ValueError):
            self.ccm.initialize(-1)


if __name__ == "__main__":
    unittest.main()
