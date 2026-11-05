from __future__ import annotations

import unittest

from common.errors import TransactionAbortedError
from common.expression import ColumnRef
from common.transaction import AccessStatus
from query_processor.operators.project import ProjectOp, SelectExecution
from query_processor.operators.scan import TableScanOp
from tests.qp.fakes import ROWS, SCHEMA, FakeCtx, FakeSM, drain


class TestTableScan(unittest.TestCase):
    def test_returns_all_rows_with_rid(self):
        ctx = FakeCtx(FakeSM())
        op = TableScanOp("t", SCHEMA)
        op.open(ctx)
        rows = drain(op)
        op.close()
        self.assertEqual([r.values for r in rows], [(i, f"n{i}") for i in range(4)])
        self.assertEqual([r.rid for r in rows], [r.rid for r in ROWS])
        self.assertEqual(ctx.calls, [("table", "t")])

    def test_next_after_close_is_none(self):
        op = TableScanOp("t", SCHEMA)
        op.open(FakeCtx(FakeSM()))
        op.close()
        self.assertIsNone(op.next())

    def test_denied_access_raises(self):
        op = TableScanOp("t", SCHEMA)
        with self.assertRaises(TransactionAbortedError):
            op.open(FakeCtx(FakeSM(), AccessStatus.ABORTED))


class TestProject(unittest.TestCase):
    def test_selects_and_reorders_columns(self):
        op = ProjectOp(TableScanOp("t", SCHEMA), [ColumnRef("t", "name"), ColumnRef("t", "id")])
        op.open(FakeCtx(FakeSM()))
        rows = drain(op)
        op.close()
        self.assertEqual([b.name for b in op.schema.columns], ["name", "id"])
        self.assertEqual(rows[1].values, ("n1", 1))
        self.assertEqual(rows[1].rid, ROWS[1].rid)


class TestSelectExecution(unittest.TestCase):
    def test_collects_columns_and_rows(self):
        root = ProjectOp(TableScanOp("t", SCHEMA), [ColumnRef("t", "name")])
        result = SelectExecution(root).execute(FakeCtx(FakeSM()))
        self.assertTrue(result.ok)
        self.assertEqual(result.columns, ["name"])
        self.assertEqual(result.rows, [["n0"], ["n1"], ["n2"], ["n3"]])


if __name__ == "__main__":
    unittest.main()