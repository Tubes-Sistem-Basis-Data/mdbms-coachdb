"""Uji operator SubqueryScanOp (QP-16, subquery di FROM)."""

from __future__ import annotations

import unittest

from common.catalog import DataType
from common.errors import StorageError
from common.expression import ColumnRef
from common.plan import JoinKind
from query_processor.errors import ExecutionError, PlanError
from query_processor.operators.join import BlockNestedLoopJoinOp
from query_processor.operators.subquery import SubqueryScanOp
from query_processor.translator import PlanTranslator
from query_processor.tuple import ColumnBinding, ExecRow, OutputSchema
from tests.qp.fakes import ROWS, FakeCtx, FakeSM, FakeSource, drain


class TrackingSource(FakeSource):
    def __init__(self, rows=ROWS, fail_open=False, fail_next=False, fail_close=False) -> None:
        super().__init__(rows)
        self.opens = 0
        self.nexts = 0
        self.closes = 0
        self._fail_open = fail_open
        self._fail_next = fail_next
        self._fail_close = fail_close

    def open(self, ctx) -> None:
        self.opens += 1
        super().open(ctx)
        if self._fail_open:
            raise StorageError("inner open failed")

    def next(self) -> ExecRow | None:
        self.nexts += 1
        if self._fail_next:
            raise StorageError("inner read failed")
        return super().next()

    def close(self) -> None:
        self.closes += 1
        super().close()
        if self._fail_close:
            raise RuntimeError("inner close failed")


def derived(child, alias: str = "t") -> SubqueryScanOp:
    """Bangun operator dengan skema yang sama seperti hasil translator."""
    return SubqueryScanOp(child, alias, PlanTranslator._subquery_schema(child.schema, alias))


class TestSubqueryScanOp(unittest.TestCase):
    def setUp(self) -> None:
        self.ctx = FakeCtx(FakeSM())

    def test_exposes_export_names_under_alias_without_rid(self) -> None:
        child = TrackingSource()
        child.schema = OutputSchema([
            ColumnBinding("student", "id", DataType.INTEGER),
            ColumnBinding("student", "name", DataType.VARCHAR),
        ], labels=("sid", "nama"))
        operator = derived(child)

        operator.open(self.ctx)
        rows = drain(operator)
        operator.close()

        self.assertEqual([b.qualifier for b in operator.schema.columns], ["t", "t"])
        self.assertEqual([b.name for b in operator.schema.columns], ["sid", "nama"])
        self.assertEqual([b.type for b in operator.schema.columns], [DataType.INTEGER, DataType.VARCHAR])
        self.assertEqual(operator.schema.output_names(), ["sid", "nama"])
        self.assertEqual([row.values for row in rows], [tuple(row.values) for row in ROWS])
        self.assertTrue(all(row.rid is None for row in rows))
        self.assertTrue(all(row.schema is operator.schema for row in rows))
        self.assertEqual(operator.schema.index_of(ColumnRef("t", "sid")), 0)
        for hidden in (ColumnRef("student", "id"), ColumnRef("t", "id"), ColumnRef("student", "sid")):
            with self.subTest(ref=hidden), self.assertRaises(PlanError):
                operator.schema.index_of(hidden)

    def test_child_schema_is_not_mutated(self) -> None:
        child = TrackingSource()
        before = (list(child.schema.columns), child.schema.labels)
        derived(child, "q")
        self.assertEqual((child.schema.columns, child.schema.labels), before)
        self.assertTrue(all(binding.qualifier == "t" for binding in child.schema.columns))

    def test_streaming_reads_one_child_row_per_next(self) -> None:
        child = TrackingSource()
        operator = derived(child)
        operator.open(self.ctx)

        self.assertEqual(operator.next().values, (0, "n0"))
        self.assertEqual(child.nexts, 1)
        operator.close()

    def test_lifecycle_reopen_idempotent_close_and_next_outside_scan(self) -> None:
        child = TrackingSource()
        operator = derived(child, "derived")
        self.assertIsNone(operator.next())
        self.assertEqual(child.nexts, 0)

        operator.open(self.ctx)
        self.assertEqual(operator.next().values, (0, "n0"))
        operator.open(self.ctx)
        self.assertEqual([row.values for row in drain(operator)], [
            (0, "n0"), (1, "n1"), (2, "n2"), (3, "n3"),
        ])
        operator.close()
        operator.close()

        self.assertEqual((child.opens, child.closes), (2, 2))
        self.assertIsNone(operator.next())

    def test_empty_child_returns_no_rows(self) -> None:
        child = TrackingSource(rows=[])
        operator = derived(child)
        operator.open(self.ctx)
        self.assertEqual(drain(operator), [])
        operator.close()
        self.assertEqual(child.closes, 1)

    def test_open_failure_closes_child_and_keeps_original_error(self) -> None:
        child = TrackingSource(fail_open=True, fail_close=True)
        operator = derived(child)

        with self.assertRaisesRegex(StorageError, "inner open failed"):
            operator.open(self.ctx)

        self.assertEqual(child.closes, 1)
        self.assertIsNone(operator.next())
        operator.close()
        self.assertEqual(child.closes, 1)

    def test_next_failure_closes_child_and_keeps_original_error(self) -> None:
        child = TrackingSource(fail_next=True, fail_close=True)
        operator = derived(child)
        operator.open(self.ctx)

        with self.assertRaisesRegex(StorageError, "inner read failed"):
            operator.next()

        self.assertEqual(child.closes, 1)
        self.assertIsNone(operator.next())

    def test_malformed_child_row_is_rejected_and_child_closed(self) -> None:
        child = TrackingSource()
        child.next = lambda: ExecRow((1,), child.schema)
        operator = derived(child)
        operator.open(self.ctx)
        with self.assertRaises(ExecutionError):
            operator.next()
        self.assertEqual(child.closes, 1)

    def test_invalid_alias_and_schema_are_rejected_before_open(self) -> None:
        child = TrackingSource()
        good = PlanTranslator._subquery_schema(child.schema, "t")
        for alias in ("", " ", None, 1):
            with self.subTest(alias=alias), self.assertRaises(PlanError):
                SubqueryScanOp(child, alias, good)

        cases = {
            "bukan OutputSchema": None,
            "lebar berbeda": OutputSchema(good.columns[:1]),
            "qualifier lain": OutputSchema([
                ColumnBinding("x", "id", DataType.INTEGER), good.columns[1],
            ]),
            "tipe berbeda": OutputSchema([
                ColumnBinding("t", "id", DataType.FLOAT), good.columns[1],
            ]),
            "nama ekspor berulang": OutputSchema([
                ColumnBinding("t", "id", DataType.INTEGER), ColumnBinding("t", "id", DataType.VARCHAR),
            ]),
            "label tampilan": OutputSchema(list(good.columns), labels=("a", "b")),
        }
        for label, schema in cases.items():
            with self.subTest(label), self.assertRaises(PlanError):
                SubqueryScanOp(child, "t", schema)
        self.assertEqual(child.opens, 0)

    def test_duplicate_export_names_are_rejected_by_translator_schema(self) -> None:
        child = TrackingSource()
        child.schema = OutputSchema([
            ColumnBinding("a", "id", DataType.INTEGER),
            ColumnBinding("b", "id", DataType.INTEGER),
        ])
        with self.assertRaisesRegex(PlanError, "berulang"):
            PlanTranslator._subquery_schema(child.schema, "q")

        child.schema = OutputSchema(list(child.schema.columns), labels=("nim", "nim"))
        with self.assertRaisesRegex(PlanError, "berulang"):
            PlanTranslator._subquery_schema(child.schema, "q")

        child.schema = OutputSchema(list(child.schema.columns), labels=("a_id", "b_id"))
        schema = PlanTranslator._subquery_schema(child.schema, "q")
        self.assertEqual([b.name for b in schema.columns], ["a_id", "b_id"])

    def test_rescan_as_inner_of_block_nested_loop_join(self) -> None:
        left_schema = OutputSchema([ColumnBinding("l", "k", DataType.INTEGER)])
        left = FakeSource([type(ROWS[0])(ROWS[i].rid, [i]) for i in range(3)])
        left.schema = left_schema
        inner_child = TrackingSource(rows=ROWS[:2])
        right = derived(inner_child, "q")
        join = BlockNestedLoopJoinOp(
            left, right, OutputSchema(left_schema.columns + right.schema.columns),
            JoinKind.CROSS, block_size=2,
        )

        join.open(self.ctx)
        rows = drain(join)
        join.close()

        self.assertCountEqual([row.values for row in rows], [
            (k, i, f"n{i}") for k in range(3) for i in range(2)
        ])
        self.assertTrue(all(row.rid is None for row in rows))
        self.assertEqual((inner_child.opens, inner_child.closes), (2, 2))


if __name__ == "__main__":
    unittest.main()
