import unittest

from concurrency_control.deadlock import DeadlockDetector, WaitForGraph


class WaitForGraphTest(unittest.TestCase):
    def test_no_cycle_when_only_waiting(self) -> None:
        g = WaitForGraph()
        g.set_waits(1, {2})
        g.set_waits(2, {3})
        self.assertIsNone(g.find_cycle(1))

    def test_cycle_members(self) -> None:
        g = WaitForGraph()
        g.set_waits(1, {2})
        g.set_waits(2, {3})
        g.set_waits(3, {1})
        self.assertEqual(sorted(g.find_cycle(1)), [1, 2, 3])

    def test_cycle_reachable_but_not_containing_start(self) -> None:
        g = WaitForGraph()
        g.set_waits(5, {1})
        g.set_waits(1, {2})
        g.set_waits(2, {1})
        self.assertEqual(sorted(g.find_cycle(5)), [1, 2])

    def test_empty_holders_keep_incoming_edges(self) -> None:
        g = WaitForGraph()
        g.set_waits(1, {2})
        g.set_waits(2, {1})
        g.set_waits(2, set())
        self.assertNotIn(2, g.edges)
        self.assertEqual(g.edges[1], {2})

    def test_remove_drops_incoming_and_outgoing(self) -> None:
        g = WaitForGraph()
        g.set_waits(1, {2})
        g.set_waits(2, {3})
        g.remove(2)
        self.assertEqual(g.edges, {})


class DeadlockDetectorTest(unittest.TestCase):
    def test_victim_is_youngest_in_cycle_only(self) -> None:
        d = DeadlockDetector()
        d.on_wait(9, {1})
        d.on_wait(1, {2})
        self.assertEqual(d.on_wait(2, {1}), 2)

    def test_no_victim_without_cycle(self) -> None:
        d = DeadlockDetector()
        self.assertIsNone(d.on_wait(1, {2}))

    def test_granted_and_finished_break_cycle(self) -> None:
        d = DeadlockDetector()
        d.on_wait(1, {2})
        d.on_granted(1)
        self.assertIsNone(d.on_wait(2, {1}))
        d.on_wait(1, {2})
        d.on_finished(2)
        self.assertEqual(d.graph.edges, {})


if __name__ == "__main__":
    unittest.main()
