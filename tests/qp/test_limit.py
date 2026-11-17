from __future__ import annotations

import unittest

from common.errors import StorageError
from query_processor.errors import PlanError
from query_processor.operators.limit import LimitOp
from query_processor.operators.project import SelectExecution
from query_processor.tuple import OutputSchema
from tests.qp.fakes import ROWS, SCHEMA, FakeCtx, FakeSM, FakeSource, drain


class TrackingSource(FakeSource):
    def __init__(self, rows=ROWS, fail_open=False, fail_next_at=None, fail_close=False):
        super().__init__(rows)
        self.opens = 0
        self.nexts = 0
        self.closes = 0
        self._fail_open = fail_open
        self._fail_next_at = fail_next_at
        self._fail_close = fail_close

    def open(self, ctx):
        self.opens += 1
        super().open(ctx)
        if self._fail_open:
            raise StorageError("gagal membuka child")

    def next(self):
        self.nexts += 1
        if self.nexts == self._fail_next_at:
            raise StorageError("gagal membaca child")
        return super().next()

    def close(self):
        self.closes += 1
        super().close()
        if self._fail_close:
            raise RuntimeError("gagal menutup child")


def run(op):
    op.open(FakeCtx(FakeSM()))
    try:
        return drain(op)
    finally:
        op.close()


class TestLimitResult(unittest.TestCase):
    def test_returns_first_rows_with_rid_and_child_schema(self):
        child = TrackingSource()
        op = LimitOp(child, 2)
        rows = run(op)
        self.assertEqual([r.values for r in rows], [(0, "n0"), (1, "n1")])
        self.assertEqual([r.rid for r in rows], [ROWS[0].rid, ROWS[1].rid])
        self.assertIs(op.schema, child.schema)

    def test_count_variants(self):
        cases = [(0, ROWS, 0), (1, ROWS, 1), (4, ROWS, 4), (10, ROWS, 4), (3, [], 0)]
        for count, data, expected in cases:
            with self.subTest(count=count, size=len(data)):
                rows = run(LimitOp(TrackingSource(data), count))
                self.assertEqual([r.values for r in rows], [tuple(r.values) for r in data[:expected]])

    def test_limit_zero_does_not_touch_child(self):
        child = TrackingSource()
        self.assertEqual(run(LimitOp(child, 0)), [])
        self.assertEqual((child.opens, child.nexts, child.closes), (0, 0, 0))

    def test_invalid_count_rejected_before_open(self):
        for count in (-1, True, False, 2.0, "2", None):
            child = TrackingSource()
            with self.subTest(count=count), self.assertRaises(PlanError):
                LimitOp(child, count)
            self.assertEqual(child.opens, 0)

    def test_keeps_labels_from_child(self):
        child = TrackingSource()
        child.schema = OutputSchema(list(SCHEMA.columns), labels=("nim", "nama"))
        self.assertEqual(LimitOp(child, 1).schema.output_names(), ["nim", "nama"])


class TestLimitLifecycle(unittest.TestCase):
    def test_no_extra_row_requested_after_limit(self):
        child = TrackingSource()
        op = LimitOp(child, 2)
        op.open(FakeCtx(FakeSM()))
        drain(op)
        self.assertIsNone(op.next())
        op.close()
        self.assertEqual(child.nexts, 2)

    def test_child_closed_once_per_open_cycle(self):
        for count in (2, 10):
            child = TrackingSource()
            op = LimitOp(child, count)
            run(op)
            op.close()
            with self.subTest(count=count):
                self.assertEqual(child.closes, 1)

    def test_next_before_open_and_after_close_is_none(self):
        child = TrackingSource()
        op = LimitOp(child, 2)
        self.assertIsNone(op.next())
        op.open(FakeCtx(FakeSM()))
        op.close()
        self.assertIsNone(op.next())
        self.assertEqual(child.nexts, 0)

    def test_reopen_starts_from_beginning(self):
        child = TrackingSource()
        op = LimitOp(child, 2)
        first = run(op)
        second = run(op)
        self.assertEqual([r.values for r in first], [r.values for r in second])
        self.assertEqual((child.opens, child.closes), (2, 2))

    def test_reopen_without_close_closes_previous_cycle(self):
        child = TrackingSource()
        op = LimitOp(child, 3)
        op.open(FakeCtx(FakeSM()))
        op.next()
        op.open(FakeCtx(FakeSM()))
        self.assertEqual([r.values for r in drain(op)], [(0, "n0"), (1, "n1"), (2, "n2")])
        self.assertEqual(child.closes, 2)

    def test_open_failure_closes_child(self):
        child = TrackingSource(fail_open=True)
        op = LimitOp(child, 2)
        with self.assertRaises(StorageError):
            op.open(FakeCtx(FakeSM()))
        op.close()
        self.assertEqual(child.closes, 1)
        self.assertIsNone(op.next())

    def test_cleanup_error_does_not_hide_open_error(self):
        child = TrackingSource(fail_open=True, fail_close=True)
        with self.assertRaises(StorageError):
            LimitOp(child, 2).open(FakeCtx(FakeSM()))

    def test_next_failure_closes_child(self):
        child = TrackingSource(fail_next_at=2)
        op = LimitOp(child, 3)
        op.open(FakeCtx(FakeSM()))
        self.assertEqual(op.next().values, (0, "n0"))
        with self.assertRaises(StorageError):
            op.next()
        op.close()
        self.assertEqual(child.closes, 1)


class TestSelectWithLimit(unittest.TestCase):
    def test_limit_root_keeps_header(self):
        for count, rows in ((2, [[0, "n0"], [1, "n1"]]), (0, [])):
            result = SelectExecution(LimitOp(TrackingSource(), count)).execute(FakeCtx(FakeSM()))
            with self.subTest(count=count):
                self.assertEqual(result.columns, ["id", "name"])
                self.assertEqual(result.rows, rows)


if __name__ == "__main__":
    unittest.main()