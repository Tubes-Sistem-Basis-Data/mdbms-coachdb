from __future__ import annotations

import unittest

from common.errors import TransactionAbortedError
from common.transaction import NO_RECORD, AccessStatus, Action, ChangeOp
from query_processor.operators.delete import DeleteExecution
from tests.qp.fakes import ROWS, FakeCtx, FakeSM, FakeSource


class TestDelete(unittest.TestCase):
    def test_deletes_source_rows_with_lsn_and_before_image(self):
        sm = FakeSM()
        ctx = FakeCtx(sm)
        source = FakeSource([ROWS[0], ROWS[2]])
        result = DeleteExecution("t", source).execute(ctx)
        self.assertTrue(result.ok)
        self.assertEqual(result.affected_rows, 2)
        self.assertEqual(sm.deleted, [(ROWS[0].rid, 7), (ROWS[2].rid, 7)])
        self.assertTrue(source.closed)
        _, rid, action, change = ctx.calls[0]
        self.assertEqual((rid, action), (ROWS[0].rid, Action.WRITE))
        self.assertEqual((change.op, change.before, change.after), (ChangeOp.DELETE, [0, "n0"], NO_RECORD))

    def test_without_where_deletes_everything(self):
        sm = FakeSM()
        result = DeleteExecution("t", FakeSource()).execute(FakeCtx(sm))
        self.assertEqual(result.affected_rows, 4)
        self.assertEqual(sm.rows, [])

    def test_empty_source_deletes_nothing(self):
        sm = FakeSM()
        result = DeleteExecution("t", FakeSource([])).execute(FakeCtx(sm))
        self.assertTrue(result.ok)
        self.assertEqual(result.affected_rows, 0)

    def test_denied_write_raises_and_deletes_nothing(self):
        sm = FakeSM()
        with self.assertRaises(TransactionAbortedError):
            DeleteExecution("t", FakeSource()).execute(FakeCtx(sm, AccessStatus.ABORTED))
        self.assertEqual(sm.deleted, [])


if __name__ == "__main__":
    unittest.main()