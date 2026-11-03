import unittest

from common.storage import RID
from common.transaction import AccessStatus, Action, ResourceId
from concurrency_control.lock_manager import LockManager, LockMode, combine, compatible
from concurrency_control.protocol import Transaction

ALLOWED, WAIT, ABORTED = AccessStatus.ALLOWED, AccessStatus.WAIT, AccessStatus.ABORTED
TABLE = ResourceId("student")


def row(slot: int, table: str = "student") -> ResourceId:
    return ResourceId(table, RID(table, 0, slot))


class LockModeTest(unittest.TestCase):
    def test_matrix_is_symmetric(self) -> None:
        for a in LockMode:
            for b in LockMode:
                self.assertEqual(compatible(a, b), compatible(b, a), (a, b))

    def test_combine(self) -> None:
        self.assertIs(combine(LockMode.S, LockMode.IX), LockMode.SIX)
        self.assertIs(combine(LockMode.IS, LockMode.S), LockMode.S)
        self.assertIs(combine(LockMode.IS, LockMode.IX), LockMode.IX)
        self.assertIs(combine(LockMode.SIX, LockMode.X), LockMode.X)


class LockManagerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.lm = LockManager()
        self.t = {i: Transaction(i, i) for i in range(1, 6)}

    def req(self, tx: int, res: ResourceId, action: Action) -> AccessStatus:
        return self.lm.request(self.t[tx], res, action).status

    def test_row_access_takes_intention_lock(self) -> None:
        self.assertIs(self.req(1, row(1), Action.READ), ALLOWED)
        self.assertEqual(self.lm.held_locks(1), {TABLE: LockMode.IS, row(1): LockMode.S})
        self.assertIs(self.req(1, row(1), Action.WRITE), ALLOWED)
        self.assertEqual(self.lm.held_locks(1), {TABLE: LockMode.IX, row(1): LockMode.X})

    def test_shared_readers(self) -> None:
        self.assertIs(self.req(1, row(1), Action.READ), ALLOWED)
        self.assertIs(self.req(2, row(1), Action.READ), ALLOWED)

    def test_writers_on_different_rows(self) -> None:
        self.assertIs(self.req(1, row(1), Action.WRITE), ALLOWED)
        self.assertIs(self.req(2, row(2), Action.WRITE), ALLOWED)

    def test_conflict_waits_until_commit(self) -> None:
        self.assertIs(self.req(1, row(1), Action.WRITE), ALLOWED)
        self.assertIs(self.req(2, row(1), Action.READ), WAIT)
        self.assertIs(self.req(2, row(1), Action.READ), WAIT)  # retry tetap WAIT
        self.lm.on_finish(1)
        self.assertIs(self.req(2, row(1), Action.READ), ALLOWED)

    def test_table_scan_blocks_insert(self) -> None:
        # S tabel vs IX dari INSERT transaksi lain: phantom tercegah
        self.assertIs(self.req(1, TABLE, Action.READ), ALLOWED)
        self.assertIs(self.req(2, row(7), Action.WRITE), WAIT)
        self.lm.on_finish(1)
        self.assertIs(self.req(2, row(7), Action.WRITE), ALLOWED)

    def test_table_lock_covers_rows(self) -> None:
        self.assertIs(self.req(1, TABLE, Action.READ), ALLOWED)
        self.assertIs(self.req(1, row(3), Action.READ), ALLOWED)
        self.assertEqual(self.lm.held_locks(1), {TABLE: LockMode.S})

    def test_scan_then_update_becomes_six(self) -> None:
        self.assertIs(self.req(1, TABLE, Action.READ), ALLOWED)
        self.assertIs(self.req(1, row(3), Action.WRITE), ALLOWED)
        self.assertEqual(self.lm.held_locks(1), {TABLE: LockMode.SIX, row(3): LockMode.X})
        self.assertIs(self.req(2, row(4), Action.READ), ALLOWED)  # IS kompatibel dengan SIX
        self.assertIs(self.req(3, row(5), Action.WRITE), WAIT)    # IX tidak

    def test_fifo_queue(self) -> None:
        self.assertIs(self.req(1, row(1), Action.READ), ALLOWED)
        self.assertIs(self.req(2, row(1), Action.WRITE), WAIT)
        # T3 baca kompatibel dengan T1, tapi antre di belakang writer T2
        self.assertIs(self.req(3, row(1), Action.READ), WAIT)
        self.lm.on_finish(1)
        self.assertIs(self.req(3, row(1), Action.READ), WAIT)
        self.assertIs(self.req(2, row(1), Action.WRITE), ALLOWED)

    def test_strict_2pl_holds_until_finish(self) -> None:
        self.assertIs(self.req(1, row(1), Action.WRITE), ALLOWED)
        self.assertIs(self.req(1, row(2), Action.WRITE), ALLOWED)
        self.assertEqual(len(self.lm.held_locks(1)), 3)
        self.lm.on_finish(1)
        self.assertEqual(self.lm.held_locks(1), {})

    def test_two_way_deadlock_aborts_youngest(self) -> None:
        self.assertIs(self.req(1, row(1), Action.WRITE), ALLOWED)
        self.assertIs(self.req(2, row(2), Action.WRITE), ALLOWED)
        self.assertIs(self.req(1, row(2), Action.WRITE), WAIT)
        self.assertIs(self.req(2, row(1), Action.WRITE), ABORTED)
        self.lm.on_finish(2)  # facade abort + rollback, lalu lepas lock
        self.assertIs(self.req(1, row(2), Action.WRITE), ALLOWED)

    def test_victim_gets_aborted_on_retry(self) -> None:
        # T2 nunggu duluan, T1 yang menutup cycle; victim T2 baru tahu pas retry
        self.assertIs(self.req(1, row(1), Action.WRITE), ALLOWED)
        self.assertIs(self.req(2, row(2), Action.WRITE), ALLOWED)
        self.assertIs(self.req(2, row(1), Action.WRITE), WAIT)
        self.assertIs(self.req(1, row(2), Action.WRITE), WAIT)
        self.assertIs(self.req(2, row(1), Action.WRITE), ABORTED)
        self.lm.on_finish(2)
        self.assertIs(self.req(1, row(2), Action.WRITE), ALLOWED)

    def test_upgrade_deadlock(self) -> None:
        self.assertIs(self.req(1, row(1), Action.READ), ALLOWED)
        self.assertIs(self.req(2, row(1), Action.READ), ALLOWED)
        self.assertIs(self.req(1, row(1), Action.WRITE), WAIT)
        self.assertIs(self.req(2, row(1), Action.WRITE), ABORTED)
        self.lm.on_finish(2)
        self.assertIs(self.req(1, row(1), Action.WRITE), ALLOWED)

    def test_three_way_deadlock(self) -> None:
        for tx in (1, 2, 3):
            self.assertIs(self.req(tx, row(tx), Action.WRITE), ALLOWED)
        self.assertIs(self.req(1, row(2), Action.WRITE), WAIT)
        self.assertIs(self.req(2, row(3), Action.WRITE), WAIT)
        self.assertIs(self.req(3, row(1), Action.WRITE), ABORTED)
        self.lm.on_finish(3)
        self.assertIs(self.req(2, row(3), Action.WRITE), ALLOWED)
        self.assertIs(self.req(1, row(2), Action.WRITE), WAIT)

    def test_waiting_without_cycle_is_not_deadlock(self) -> None:
        self.assertIs(self.req(1, row(1), Action.WRITE), ALLOWED)
        for _ in range(3):
            self.assertIs(self.req(2, row(1), Action.WRITE), WAIT)
        self.assertIs(self.req(3, row(1), Action.WRITE), WAIT)

    def test_finish_of_waiting_tx_clears_queue(self) -> None:
        self.assertIs(self.req(1, row(1), Action.WRITE), ALLOWED)
        self.assertIs(self.req(2, row(1), Action.WRITE), WAIT)
        self.lm.on_finish(2)  # user abort saat masih antre
        self.lm.on_finish(1)
        self.assertIs(self.req(3, row(1), Action.WRITE), ALLOWED)


if __name__ == "__main__":
    unittest.main()
