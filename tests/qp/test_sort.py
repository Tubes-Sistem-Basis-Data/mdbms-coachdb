from __future__ import annotations

import unittest

from common.catalog import DataType
from common.expression import ColumnRef
from common.storage import RID
from query_processor.context import ExecContext
from query_processor.errors import ExecutionError, PlanError
from query_processor.operators.base import RowOperator
from query_processor.operators.sort import SortOp
from query_processor.tuple import ColumnBinding, ExecRow, OutputSchema


class ListOperator(RowOperator):
    def __init__(
        self,
        schema: OutputSchema,
        rows: list[tuple],
        fail_on_next: bool = False,
    ) -> None:
        self.schema = schema
        self._rows = [
            ExecRow(values, schema, RID("items", 0, i))
            for i, values in enumerate(rows)
        ]
        self._cursor = 0
        self._fail_on_next = fail_on_next
        self.open_calls = 0
        self.next_calls = 0
        self.close_calls = 0

    def open(self, ctx: ExecContext) -> None:
        self.open_calls += 1
        self._cursor = 0

    def next(self) -> ExecRow | None:
        self.next_calls += 1
        if self._fail_on_next:
            raise ExecutionError("test child failure")
        if self._cursor >= len(self._rows):
            return None
        row = self._rows[self._cursor]
        self._cursor += 1
        return row

    def close(self) -> None:
        self.close_calls += 1


def drain(operator: RowOperator) -> list[ExecRow]:
    rows: list[ExecRow] = []
    while (row := operator.next()) is not None:
        rows.append(row)
    return rows


class TestSortOp(unittest.TestCase):
    def setUp(self) -> None:
        self.ctx = ExecContext(tx_id=1, sm=None, ccm=None)

    def test_ascending_sorts_numbers_by_value_and_keeps_null_last(self) -> None:
        schema = OutputSchema([
            ColumnBinding("items", "score", DataType.INTEGER),
            ColumnBinding("items", "label", DataType.VARCHAR),
        ])
        child = ListOperator(schema, [(10, "ten"), (2, "two"), (None, "null")])
        operator = SortOp(child, ColumnRef("items", "score"))

        operator.open(self.ctx)
        rows = drain(operator)
        operator.close()

        self.assertEqual([row.values for row in rows], [
            (2, "two"), (10, "ten"), (None, "null"),
        ])
        self.assertEqual(child.close_calls, 1)

    def test_descending_keeps_null_first_and_sort_is_stable_for_ties(self) -> None:
        schema = OutputSchema([
            ColumnBinding("items", "score", DataType.INTEGER),
            ColumnBinding("items", "label", DataType.VARCHAR),
        ])
        child = ListOperator(schema, [
            (2, "first"), (1, "one"), (2, "second"), (None, "null"),
        ])
        operator = SortOp(child, ColumnRef("items", "score"), ascending=False)

        operator.open(self.ctx)
        rows = drain(operator)
        operator.close()

        self.assertEqual([row.values for row in rows], [
            (None, "null"), (2, "first"), (2, "second"), (1, "one"),
        ])

    def test_text_sorts_lexicographically_including_special_characters(self) -> None:
        schema = OutputSchema([ColumnBinding("items", "label", DataType.VARCHAR)])
        child = ListOperator(schema, [("é",), ("b",), ("!",), ("a",)])
        operator = SortOp(child, ColumnRef("items", "label"))

        operator.open(self.ctx)
        rows = drain(operator)
        operator.close()

        self.assertEqual([row.values[0] for row in rows], ["!", "a", "b", "é"])

    def test_empty_child_returns_no_rows(self) -> None:
        schema = OutputSchema([ColumnBinding("items", "score", DataType.INTEGER)])
        child = ListOperator(schema, [])
        operator = SortOp(child, ColumnRef("items", "score"))

        operator.open(self.ctx)
        self.assertEqual(drain(operator), [])
        operator.close()
        self.assertEqual(child.close_calls, 1)

    def test_sort_preserves_original_rows_and_rids(self) -> None:
        schema = OutputSchema([ColumnBinding("items", "score", DataType.INTEGER)])
        child = ListOperator(schema, [(3,), (1,), (2,)])
        source_rows = list(child._rows)
        operator = SortOp(child, ColumnRef("items", "score"))

        operator.open(self.ctx)
        rows = drain(operator)
        operator.close()

        self.assertEqual([row.values for row in rows], [(1,), (2,), (3,)])
        self.assertEqual([row.rid for row in rows], [
            source_rows[1].rid, source_rows[2].rid, source_rows[0].rid,
        ])
        self.assertEqual(child._rows, source_rows)

    def test_child_is_closed_if_reading_fails(self) -> None:
        schema = OutputSchema([ColumnBinding("items", "score", DataType.INTEGER)])
        child = ListOperator(schema, [], fail_on_next=True)
        operator = SortOp(child, ColumnRef("items", "score"))

        with self.assertRaisesRegex(ExecutionError, "test child failure"):
            operator.open(self.ctx)

        self.assertEqual(child.close_calls, 1)

    def test_incomparable_values_raise_execution_error_and_close_child(self) -> None:
        schema = OutputSchema([ColumnBinding("items", "score", DataType.INTEGER)])
        child = ListOperator(schema, [(1,), ("two",)])
        operator = SortOp(child, ColumnRef("items", "score"))

        with self.assertRaisesRegex(ExecutionError, "ORDER BY"):
            operator.open(self.ctx)

        self.assertEqual(child.close_calls, 1)

    def test_invalid_column_and_direction_are_rejected(self) -> None:
        schema = OutputSchema([ColumnBinding("items", "score", DataType.INTEGER)])
        child = ListOperator(schema, [(1,)])
        with self.assertRaises(PlanError):
            SortOp(child, ColumnRef("items", "missing"))
        with self.assertRaises(PlanError):
            SortOp(child, ColumnRef("items", "score"), ascending=1)


if __name__ == "__main__":
    unittest.main()
