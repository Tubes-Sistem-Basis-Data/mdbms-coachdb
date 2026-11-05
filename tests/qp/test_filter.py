from __future__ import annotations

import unittest

from common.catalog import DataType
from common.expression import ColumnRef, CompOp, Comparison, Literal
from query_processor.operators.filter import FilterOp
from tests.qp.fakes import SCHEMA, FakeCtx, FakeSM, FakeSource, drain


def id_ge(value: int) -> Comparison:
    return Comparison(CompOp.GE, ColumnRef("t", "id"), Literal(value, DataType.INTEGER))


class TestFilter(unittest.TestCase):
    def test_keeps_only_matching_rows(self):
        child = FakeSource()
        op = FilterOp(child, id_ge(2))
        op.open(FakeCtx(FakeSM()))
        ids = [r.values[0] for r in drain(op)]
        op.close()
        self.assertEqual(ids, [2, 3])
        self.assertTrue(child.closed)

    def test_no_match_is_empty_not_error(self):
        op = FilterOp(FakeSource(), id_ge(99))
        op.open(FakeCtx(FakeSM()))
        self.assertEqual(drain(op), [])
        op.close()

    def test_schema_follows_child(self):
        self.assertIs(FilterOp(FakeSource(), id_ge(0)).schema, SCHEMA)


if __name__ == "__main__":
    unittest.main()