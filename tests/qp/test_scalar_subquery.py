from __future__ import annotations

import copy
import unittest
from unittest.mock import Mock

from common.catalog import DataType
from common.errors import StorageError
from common.expression import And, ColumnRef, Comparison, CompOp, Literal, Subquery
from common.interfaces import ConcurrencyControlManager, StorageManager
from common.plan import Project, TableScan
from query_processor.context import ExecContext
from query_processor.errors import ExecutionError, PlanError
from query_processor.evaluator import ExpressionEvaluator
from query_processor.operators.base import RowOperator
from query_processor.operators.filter import FilterOp
from query_processor.subqueries import run_scalar
from query_processor.tuple import ColumnBinding, ExecRow, OutputSchema
from tests.qp.fakes import FakeSource, drain


def scalar() -> Subquery:
    return Subquery(Project(columns=[ColumnRef("t", "id")], children=[TableScan(table="t")]))


class TestScalarContextAndEvaluator(unittest.TestCase):
    def setUp(self) -> None:
        self.sm = Mock(spec=StorageManager)
        self.ccm = Mock(spec=ConcurrencyControlManager)
        self.resolver = Mock(return_value=(2, DataType.INTEGER))
        self.ctx = ExecContext(7, self.sm, self.ccm, scalar_resolver=self.resolver)
        self.expr = scalar()
        self.row = ExecRow((), OutputSchema([]))
        self.evaluator = ExpressionEvaluator(self.ctx.resolve_scalar)

    def test_cache_uses_node_identity_and_does_not_mutate_the_plan(self) -> None:
        original = copy.deepcopy(self.expr)
        self.assertEqual(self.ctx.resolve_scalar(self.expr), (2, DataType.INTEGER))
        self.assertEqual(self.ctx.resolve_scalar(self.expr), (2, DataType.INTEGER))
        self.resolver.assert_called_once_with(self.expr)
        self.assertEqual(self.expr, original)

        other = copy.deepcopy(self.expr)
        self.ctx.resolve_scalar(other)
        self.assertEqual(self.resolver.call_count, 2)

    def test_cache_is_not_shared_between_statements_or_sessions(self) -> None:
        self.ctx.resolve_scalar(self.expr)
        for tx_id in (7, 8):
            resolver = Mock(return_value=(9, DataType.INTEGER))
            other = ExecContext(tx_id, self.sm, self.ccm, scalar_resolver=resolver)
            self.assertEqual(other.resolve_scalar(self.expr), (9, DataType.INTEGER))
            resolver.assert_called_once_with(self.expr)
        self.assertEqual(self.ctx.resolve_scalar(self.expr), (2, DataType.INTEGER))
        self.assertEqual(self.ccm.mock_calls, [])

    def test_failed_resolution_is_not_cached(self) -> None:
        error = StorageError("read gagal")
        self.resolver.side_effect = [error, (2, DataType.INTEGER)]
        with self.assertRaises(StorageError) as raised:
            self.ctx.resolve_scalar(self.expr)
        self.assertIs(raised.exception, error)
        self.assertEqual(self.ctx.resolve_scalar(self.expr), (2, DataType.INTEGER))
        self.assertEqual(self.resolver.call_count, 2)

    def test_resolver_requires_a_data_transaction(self) -> None:
        self.ctx.tx_id = None
        with self.assertRaises(ExecutionError):
            self.ctx.resolve_scalar(self.expr)
        self.resolver.assert_not_called()

    def test_missing_resolver_reports_execution_error(self) -> None:
        with self.assertRaisesRegex(ExecutionError, "Resolver"):
            ExpressionEvaluator().eval(self.expr, self.row)
        with self.assertRaisesRegex(ExecutionError, "Resolver"):
            ExecContext(7, self.sm, self.ccm).resolve_scalar(self.expr)

    def test_typed_null_char_and_numeric_values_follow_existing_rules(self) -> None:
        cases = (
            ((None, DataType.FLOAT), Literal(1, DataType.INTEGER), CompOp.GT, False),
            (("IF  ", DataType.CHAR), Literal("IF", DataType.VARCHAR), CompOp.EQ, True),
            ((1.5, DataType.FLOAT), Literal(1, DataType.INTEGER), CompOp.GT, True),
        )
        for value, right, op, expected in cases:
            with self.subTest(value=value):
                evaluator = ExpressionEvaluator(lambda expr: value)
                self.assertIs(evaluator.eval_bool(Comparison(op, self.expr, right), self.row), expected)

    def test_and_short_circuits_without_executing_an_unused_scalar(self) -> None:
        expr = And([
            Comparison(CompOp.EQ, Literal(0, DataType.INTEGER), Literal(1, DataType.INTEGER)),
            Comparison(CompOp.EQ, self.expr, Literal(2, DataType.INTEGER)),
        ])
        self.assertFalse(self.evaluator.eval_bool(expr, self.row))
        self.resolver.assert_not_called()

    def test_filter_uses_the_injected_resolver_once_for_all_rows(self) -> None:
        source = FakeSource()
        operator = FilterOp(source, Comparison(CompOp.GE, ColumnRef("t", "id"), self.expr),
                            evaluator=self.evaluator)
        try:
            operator.open(self.ctx)
            rows = drain(operator)
        finally:
            operator.close()

        self.assertEqual([row.values[0] for row in rows], [2, 3])
        self.resolver.assert_called_once_with(self.expr)
        self.assertTrue(source.closed)

    def test_invalid_resolver_result_is_not_silently_coerced(self) -> None:
        for result in (None, (1,), [1, DataType.INTEGER]):
            with self.subTest(result=result), self.assertRaises(ExecutionError):
                ExpressionEvaluator(lambda expr: result).eval(self.expr, self.row)


class TestScalarRunner(unittest.TestCase):
    def setUp(self) -> None:
        self.ctx = ExecContext(7, Mock(spec=StorageManager), Mock(spec=ConcurrencyControlManager))
        self.expr = scalar()
        self.schema = OutputSchema([ColumnBinding("t", "id", DataType.INTEGER)])
        self.root = Mock(spec=RowOperator)
        self.root.schema = self.schema
        self.build = Mock(return_value=self.root)

    def test_empty_result_retains_the_column_type_and_closes(self) -> None:
        self.root.next.return_value = None

        self.assertEqual(run_scalar(self.expr, self.ctx, self.build), (None, DataType.INTEGER))

        self.build.assert_called_once_with(self.expr.plan, self.ctx)
        self.root.open.assert_called_once_with(self.ctx)
        self.root.next.assert_called_once()
        self.root.close.assert_called_once()

    def test_one_row_and_null_value_are_valid(self) -> None:
        for value in (2, None):
            with self.subTest(value=value):
                self.root.reset_mock()
                self.root.next.side_effect = [ExecRow((value,), self.schema), None]
                self.assertEqual(run_scalar(self.expr, self.ctx, self.build), (value, DataType.INTEGER))
                self.assertEqual(self.root.next.call_count, 2)
                self.root.close.assert_called_once()

    def test_second_row_rejects_cardinality_without_reading_a_third(self) -> None:
        self.root.next.side_effect = [ExecRow((1,), self.schema), ExecRow((2,), self.schema),
                                      AssertionError("baris ketiga tidak boleh dibaca")]
        with self.assertRaisesRegex(ExecutionError, "lebih dari satu baris"):
            run_scalar(self.expr, self.ctx, self.build)
        self.assertEqual(self.root.next.call_count, 2)
        self.root.close.assert_called_once()

    def test_multiple_columns_are_rejected_before_open(self) -> None:
        self.root.schema = OutputSchema(self.schema.columns * 2)
        with self.assertRaisesRegex(PlanError, "satu kolom"):
            run_scalar(self.expr, self.ctx, self.build)
        self.root.open.assert_not_called()
        self.root.next.assert_not_called()
        self.root.close.assert_called_once()

    def test_partial_open_or_next_failure_preserves_primary_error(self) -> None:
        for failing_method in ("open", "next"):
            with self.subTest(method=failing_method):
                self.root.reset_mock(side_effect=True)
                error = StorageError("read gagal")
                getattr(self.root, failing_method).side_effect = error
                self.root.close.side_effect = StorageError("close gagal")
                with self.assertRaises(StorageError) as raised:
                    run_scalar(self.expr, self.ctx, self.build)
                self.assertIs(raised.exception, error)
                self.root.close.assert_called_once()

    def test_close_failure_after_success_is_reported(self) -> None:
        self.root.next.return_value = None
        self.root.close.side_effect = StorageError("close gagal")
        with self.assertRaisesRegex(StorageError, "close gagal"):
            run_scalar(self.expr, self.ctx, self.build)

    def test_non_select_plan_is_rejected_without_building(self) -> None:
        with self.assertRaises(PlanError):
            run_scalar(Subquery(TableScan(table="t")), self.ctx, self.build)
        self.build.assert_not_called()


if __name__ == "__main__":
    unittest.main()
