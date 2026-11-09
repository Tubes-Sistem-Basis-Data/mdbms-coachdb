from __future__ import annotations

import unittest

from common.catalog import DataType
from common.expression import ColumnRef, CompOp, Comparison
from common.plan import JoinKind
from query_processor.context import ExecContext
from query_processor.errors import ExecutionError, PlanError
from query_processor.operators.base import RowOperator
from query_processor.operators.join import BlockNestedLoopJoinOp
from query_processor.tuple import ColumnBinding, ExecRow, OutputSchema


class ListOperator(RowOperator):
    def __init__(
        self,
        schema: OutputSchema,
        rows: list[tuple],
        fail_on_next: bool = False,
    ) -> None:
        self.schema = schema
        self._rows = [ExecRow(row, schema) for row in rows]
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


class TestBlockNestedLoopJoin(unittest.TestCase):
    def setUp(self) -> None:
        self.ctx = ExecContext(tx_id=1, sm=None, ccm=None)

    def test_cross_join_rescans_right_for_each_bounded_left_block(self) -> None:
        left_schema = OutputSchema([ColumnBinding("l", "id", DataType.INTEGER)])
        right_schema = OutputSchema([ColumnBinding("r", "id", DataType.INTEGER)])
        left = ListOperator(left_schema, [(1,), (2,), (3,)])
        right = ListOperator(right_schema, [(10,), (20,)])
        output_schema = OutputSchema(left_schema.columns + right_schema.columns)
        operator = BlockNestedLoopJoinOp(
            left, right, output_schema, JoinKind.CROSS, block_size=2,
        )

        operator.open(self.ctx)
        self.assertEqual((right.open_calls, right.close_calls), (0, 0))
        rows = drain(operator)
        operator.close()

        self.assertEqual([row.values for row in rows], [
            (1, 10), (2, 10), (1, 20), (2, 20), (3, 10), (3, 20),
        ])
        self.assertTrue(all(row.rid is None for row in rows))
        self.assertEqual((left.open_calls, left.close_calls), (1, 1))
        self.assertEqual((right.open_calls, right.close_calls), (2, 2))

    def test_theta_join_evaluates_on_the_combined_row(self) -> None:
        left_schema = OutputSchema([ColumnBinding("student", "id", DataType.INTEGER)])
        right_schema = OutputSchema([
            ColumnBinding("enroll", "student_id", DataType.INTEGER),
        ])
        left = ListOperator(left_schema, [(1,), (2,), (3,)])
        right = ListOperator(right_schema, [(2,), (3,), (4,)])
        condition = Comparison(
            CompOp.EQ,
            ColumnRef("student", "id"),
            ColumnRef("enroll", "student_id"),
        )
        operator = BlockNestedLoopJoinOp(
            left,
            right,
            OutputSchema(left_schema.columns + right_schema.columns),
            JoinKind.THETA,
            condition,
            block_size=1,
        )

        operator.open(self.ctx)
        rows = drain(operator)
        operator.close()

        self.assertEqual([row.values for row in rows], [(2, 2), (3, 3)])

    def test_nested_join_combines_three_inputs(self) -> None:
        student_schema = OutputSchema([
            ColumnBinding("student", "id", DataType.INTEGER),
            ColumnBinding("student", "name", DataType.VARCHAR),
        ])
        enroll_schema = OutputSchema([
            ColumnBinding("enroll", "student_id", DataType.INTEGER),
            ColumnBinding("enroll", "course_id", DataType.INTEGER),
        ])
        course_schema = OutputSchema([
            ColumnBinding("course", "id", DataType.INTEGER),
            ColumnBinding("course", "title", DataType.VARCHAR),
        ])
        students = ListOperator(student_schema, [(1, "Ada"), (2, "Bo")])
        enrollments = ListOperator(enroll_schema, [(1, 10), (2, 20), (2, 99)])
        courses = ListOperator(course_schema, [(10, "DB"), (20, "OS")])

        student_enroll_schema = OutputSchema(student_schema.columns + enroll_schema.columns)
        student_enroll = BlockNestedLoopJoinOp(
            students,
            enrollments,
            student_enroll_schema,
            JoinKind.THETA,
            Comparison(
                CompOp.EQ,
                ColumnRef("student", "id"),
                ColumnRef("enroll", "student_id"),
            ),
        )
        all_columns_schema = OutputSchema(student_enroll_schema.columns + course_schema.columns)
        three_table_join = BlockNestedLoopJoinOp(
            student_enroll,
            courses,
            all_columns_schema,
            JoinKind.THETA,
            Comparison(
                CompOp.EQ,
                ColumnRef("enroll", "course_id"),
                ColumnRef("course", "id"),
            ),
        )

        three_table_join.open(self.ctx)
        rows = drain(three_table_join)
        three_table_join.close()

        self.assertEqual([row.values for row in rows], [
            (1, "Ada", 1, 10, 10, "DB"),
            (2, "Bo", 2, 20, 20, "OS"),
        ])

    def test_natural_join_matches_all_shared_columns_and_deduplicates_them(self) -> None:
        left_schema = OutputSchema([
            ColumnBinding("student", "id", DataType.INTEGER, ("course",)),
            ColumnBinding("student", "dept", DataType.CHAR, ("course",)),
            ColumnBinding("student", "name", DataType.VARCHAR),
        ])
        right_schema = OutputSchema([
            ColumnBinding("course", "dept", DataType.VARCHAR, ("student",)),
            ColumnBinding("course", "id", DataType.INTEGER, ("student",)),
            ColumnBinding("course", "title", DataType.VARCHAR),
        ])
        left = ListOperator(left_schema, [
            (1, "CS ", "Ada"), (2, "Math ", "Bo"), (3, None, "Cy"),
        ])
        right = ListOperator(right_schema, [
            ("CS", 1, "DB"), ("Math", 2, "Calculus"), (None, 3, "No match"),
        ])
        output_schema = OutputSchema([
            left_schema.columns[0], left_schema.columns[1], left_schema.columns[2],
            right_schema.columns[2],
        ])
        operator = BlockNestedLoopJoinOp(
            left,
            right,
            output_schema,
            JoinKind.NATURAL,
            shared_columns=((1, 0), (0, 1)),
            block_size=2,
        )

        operator.open(self.ctx)
        rows = drain(operator)
        operator.close()

        self.assertEqual([row.values for row in rows], [
            (1, "CS ", "Ada", "DB"),
            (2, "Math ", "Bo", "Calculus"),
        ])
        self.assertEqual(len(rows[0].values), len(operator.schema.columns))

    def test_natural_join_without_shared_columns_is_cartesian(self) -> None:
        left_schema = OutputSchema([ColumnBinding("l", "left_id", DataType.INTEGER)])
        right_schema = OutputSchema([ColumnBinding("r", "right_id", DataType.INTEGER)])
        left = ListOperator(left_schema, [(1,), (2,)])
        right = ListOperator(right_schema, [(10,), (20,)])
        operator = BlockNestedLoopJoinOp(
            left,
            right,
            OutputSchema(left_schema.columns + right_schema.columns),
            JoinKind.NATURAL,
        )

        operator.open(self.ctx)
        rows = drain(operator)
        operator.close()

        self.assertEqual(len(rows), 4)

    def test_empty_outer_input_does_not_open_inner_input(self) -> None:
        schema = OutputSchema([ColumnBinding("t", "id", DataType.INTEGER)])
        left = ListOperator(schema, [])
        right = ListOperator(schema, [(1,)])
        operator = BlockNestedLoopJoinOp(
            left, right, OutputSchema(schema.columns + schema.columns), JoinKind.CROSS,
        )

        operator.open(self.ctx)
        self.assertEqual(drain(operator), [])
        operator.close()

        self.assertEqual(left.close_calls, 1)
        self.assertEqual((right.open_calls, right.close_calls), (0, 0))

    def test_child_failure_during_rescan_closes_both_children(self) -> None:
        schema = OutputSchema([ColumnBinding("t", "id", DataType.INTEGER)])
        left = ListOperator(schema, [(1,)])
        right = ListOperator(schema, [], fail_on_next=True)
        operator = BlockNestedLoopJoinOp(
            left, right, OutputSchema(schema.columns + schema.columns), JoinKind.CROSS,
        )

        operator.open(self.ctx)
        with self.assertRaisesRegex(ExecutionError, "test child failure"):
            drain(operator)

        self.assertEqual(left.close_calls, 1)
        self.assertEqual(right.close_calls, 1)

    def test_early_close_closes_active_inner_scan_and_outer(self) -> None:
        schema = OutputSchema([ColumnBinding("t", "id", DataType.INTEGER)])
        left = ListOperator(schema, [(1,), (2,)])
        right = ListOperator(schema, [(10,), (20,)])
        operator = BlockNestedLoopJoinOp(
            left, right, OutputSchema(schema.columns + schema.columns),
            JoinKind.CROSS, block_size=2,
        )

        operator.open(self.ctx)
        self.assertEqual(operator.next().values, (1, 10))
        operator.close()

        self.assertEqual((left.close_calls, right.close_calls), (1, 1))

    def test_invalid_block_size_and_output_schema_are_rejected(self) -> None:
        schema = OutputSchema([ColumnBinding("t", "id", DataType.INTEGER)])
        left = ListOperator(schema, [(1,)])
        right = ListOperator(schema, [(2,)])
        with self.assertRaises(PlanError):
            BlockNestedLoopJoinOp(left, right, schema, JoinKind.CROSS)
        with self.assertRaises(PlanError):
            BlockNestedLoopJoinOp(
                left, right, OutputSchema(schema.columns + schema.columns),
                JoinKind.CROSS, block_size=0,
            )


if __name__ == "__main__":
    unittest.main()
