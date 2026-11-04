"""Uji evaluasi ekspresi QP-04."""

from __future__ import annotations

import copy
import unittest
from concurrent.futures import ThreadPoolExecutor

from common.catalog import DataType
from common.errors import MdbmsError, SemanticError
from common.expression import (
    And, ArithOp, BinaryOp, ColumnRef, Comparison, CompOp, Expression, Literal,
)
from common.storage import RID
from query_processor.errors import ExecutionError, PlanError
from query_processor.evaluator import ExpressionEvaluator
from query_processor.tuple import ColumnBinding, ExecRow, OutputSchema


MIN_INTEGER = -(1 << 63)
MAX_INTEGER = (1 << 63) - 1


def literal(value: int | float | str | None) -> Literal:
    data_type = DataType.VARCHAR if isinstance(value, str) else DataType.INTEGER
    if isinstance(value, float):
        data_type = DataType.FLOAT
    return Literal(value, data_type)


def compare(left: Expression, right: Expression, op: CompOp = CompOp.EQ) -> Comparison:
    return Comparison(op, left, right)


class EvaluatorCase(unittest.TestCase):
    def setUp(self) -> None:
        self.evaluator = ExpressionEvaluator()
        self.row = ExecRow(
            (7, 3.5, 'IF  ', 'Ada', 9, 3),
            OutputSchema([
                ColumnBinding('student', 'id', DataType.INTEGER),
                ColumnBinding('student', 'gpa', DataType.FLOAT),
                ColumnBinding('student', 'dept', DataType.CHAR),
                ColumnBinding('student', 'name', DataType.VARCHAR),
                ColumnBinding('course', 'id', DataType.INTEGER),
                ColumnBinding('course', 'credits', DataType.INTEGER),
            ]),
            RID('student', 0, 2),
        )

    def evaluate(self, expr: Expression):
        return self.evaluator.eval(expr, self.row)


class TestValuesAndColumns(EvaluatorCase):
    def test_literal_values_and_types(self) -> None:
        for value in (0, -7, MIN_INTEGER, MAX_INTEGER, 1.25, '', 'A;b', None):
            with self.subTest(value=value):
                result = self.evaluate(literal(value))
                self.assertEqual(result, value)
                self.assertIs(type(result), type(value))
        result = self.evaluate(Literal(2, DataType.FLOAT))
        self.assertEqual(result, 2.0)
        self.assertIs(type(result), float)

    def test_columns_are_resolved_by_schema_position(self) -> None:
        for table, name, expected in (
            ('student', 'id', 7), ('student', 'gpa', 3.5), ('student', 'name', 'Ada'),
            ('course', 'id', 9), (None, 'credits', 3),
        ):
            with self.subTest(table=table, column=name):
                self.assertEqual(self.evaluate(ColumnRef(table, name)), expected)

    def test_missing_and_ambiguous_columns_raise_plan_error(self) -> None:
        for ref in (ColumnRef(None, 'id'), ColumnRef('unknown', 'id'), ColumnRef('student', 'missing')):
            with self.subTest(ref=ref), self.assertRaises(PlanError):
                self.evaluate(ref)

    def test_natural_join_shared_column_supports_each_origin(self) -> None:
        row = ExecRow((7,), OutputSchema([
            ColumnBinding('student', 'id', DataType.INTEGER, ('course', 'enroll')),
        ]))
        for table in ('student', 'course', 'enroll', None):
            with self.subTest(table=table):
                self.assertEqual(self.evaluator.eval(ColumnRef(table, 'id'), row), 7)

    def test_reordered_schema_does_not_reuse_wrong_column_position(self) -> None:
        row = ExecRow(('Other', 42), OutputSchema([
            ColumnBinding('student', 'name', DataType.VARCHAR),
            ColumnBinding('student', 'id', DataType.INTEGER),
        ]))
        ref = ColumnRef('student', 'id')
        self.assertEqual(self.evaluate(ref), 7)
        self.assertEqual(self.evaluator.eval(ref, row), 42)
        self.assertEqual(self.evaluate(ref), 7)

    def test_char_removes_only_right_padding_and_varchar_keeps_spaces(self) -> None:
        self.assertEqual(self.evaluate(ColumnRef('student', 'dept')), 'IF')
        self.assertEqual(self.evaluate(Literal('  IF  ', DataType.CHAR)), '  IF')
        self.assertEqual(self.evaluate(Literal('IF\t ', DataType.CHAR)), 'IF\t')
        self.assertEqual(self.evaluate(literal('IF  ')), 'IF  ')

    def test_null_column_returns_none(self) -> None:
        self.row.values = (7, None, None, 'Ada', 9, 3)
        for name in ('gpa', 'dept'):
            with self.subTest(name=name):
                self.assertIsNone(self.evaluate(ColumnRef('student', name)))

    def test_evaluation_does_not_mutate_row_schema_or_expression(self) -> None:
        expr = And([
            compare(ColumnRef('student', 'dept'), literal('IF ')),
            compare(ColumnRef('student', 'gpa'), literal(3.0), CompOp.GT),
        ])
        before = copy.deepcopy((self.row, expr))
        self.assertTrue(self.evaluator.eval_bool(expr, self.row))
        self.assertEqual((self.row, expr), before)

    def test_evaluator_can_be_shared_between_threads(self) -> None:
        expr = BinaryOp(ArithOp.MUL, ColumnRef('t', 'n'), literal(2))

        def evaluate_number(number: int):
            row = ExecRow((number,), OutputSchema([ColumnBinding('t', 'n', DataType.INTEGER)]))
            return self.evaluator.eval(expr, row)

        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(list(pool.map(evaluate_number, range(30))), list(range(0, 60, 2)))


class TestArithmetic(EvaluatorCase):
    def test_all_integer_operators(self) -> None:
        for op, expected in ((ArithOp.ADD, 9), (ArithOp.SUB, 5), (ArithOp.MUL, 14), (ArithOp.DIV, 3)):
            with self.subTest(op=op):
                result = self.evaluate(BinaryOp(op, literal(7), literal(2)))
                self.assertEqual(result, expected)
                self.assertIs(type(result), int)

    def test_float_and_mixed_operands_return_float(self) -> None:
        for left, right in ((7.0, 2.0), (7, 2.0), (7.0, 2)):
            for op, expected in ((ArithOp.ADD, 9.0), (ArithOp.SUB, 5.0), (ArithOp.MUL, 14.0), (ArithOp.DIV, 3.5)):
                with self.subTest(left=left, right=right, op=op):
                    result = self.evaluate(BinaryOp(op, literal(left), literal(right)))
                    self.assertEqual(result, expected)
                    self.assertIs(type(result), float)

    def test_integer_division_truncates_toward_zero_exactly(self) -> None:
        for left, right, expected in (
            (-7, 2, -3), (7, -2, -3), (-7, -2, 3), (-1, 2, 0), (0, -3, 0),
            (MAX_INTEGER, 1, MAX_INTEGER), (MIN_INTEGER, 1, MIN_INTEGER),
            (MAX_INTEGER, 3, 3074457345618258602), (MIN_INTEGER, 3, -3074457345618258602),
        ):
            with self.subTest(left=left, right=right):
                self.assertEqual(self.evaluate(BinaryOp(ArithOp.DIV, literal(left), literal(right))), expected)

    def test_nested_arithmetic_follows_expression_tree(self) -> None:
        expr = BinaryOp(ArithOp.MUL, BinaryOp(ArithOp.ADD, literal(2), literal(3)), literal(4))
        self.assertEqual(self.evaluate(expr), 20)
        expr = BinaryOp(ArithOp.ADD, literal(2), BinaryOp(ArithOp.MUL, literal(3), literal(4)))
        self.assertEqual(self.evaluate(expr), 14)

    def test_column_expression_and_column_to_column_arithmetic(self) -> None:
        expr = BinaryOp(ArithOp.MUL, ColumnRef('student', 'gpa'), literal(1.05))
        self.assertAlmostEqual(self.evaluate(expr), 3.675)
        expr = BinaryOp(ArithOp.SUB, ColumnRef('course', 'id'), ColumnRef('student', 'id'))
        self.assertEqual(self.evaluate(expr), 2)

    def test_salary_example_combines_arithmetic_and_comparison(self) -> None:
        row = ExecRow((6000,), OutputSchema([ColumnBinding('employee', 'salary', DataType.INTEGER)]))
        expr = compare(BinaryOp(ArithOp.MUL, ColumnRef('employee', 'salary'), literal(1.05)),
                       literal(5000), CompOp.GT)
        self.assertTrue(self.evaluator.eval_bool(expr, row))

    def test_null_arithmetic_returns_null(self) -> None:
        for op in ArithOp:
            for left, right in ((None, 2), (2, None), (None, None), (None, 0)):
                with self.subTest(op=op, left=left, right=right):
                    self.assertIsNone(self.evaluate(BinaryOp(op, literal(left), literal(right))))

    def test_division_by_zero_raises_execution_error(self) -> None:
        for left in (0, 7, -7, 1.5):
            for zero in (0, 0.0, -0.0):
                with self.subTest(left=left, zero=zero), self.assertRaisesRegex(ExecutionError, 'nol'):
                    self.evaluate(BinaryOp(ArithOp.DIV, literal(left), literal(zero)))

    def test_integer_overflow_is_checked_at_each_operation(self) -> None:
        cases = (
            BinaryOp(ArithOp.ADD, literal(MAX_INTEGER), literal(1)),
            BinaryOp(ArithOp.SUB, literal(MIN_INTEGER), literal(1)),
            BinaryOp(ArithOp.MUL, literal(MAX_INTEGER), literal(2)),
            BinaryOp(ArithOp.DIV, literal(MIN_INTEGER), literal(-1)),
            BinaryOp(ArithOp.SUB, BinaryOp(ArithOp.ADD, literal(MAX_INTEGER), literal(1)), literal(1)),
        )
        for expr in cases:
            with self.subTest(expr=expr), self.assertRaisesRegex(ExecutionError, '64-bit'):
                self.evaluate(expr)

    def test_float_overflow_raises_execution_error(self) -> None:
        for op, left, right in ((ArithOp.MUL, 1e308, 2.0), (ArithOp.ADD, 1e308, 1e308),
                                (ArithOp.DIV, 1e308, 1e-308)):
            with self.subTest(op=op), self.assertRaises(ExecutionError):
                self.evaluate(BinaryOp(op, literal(left), literal(right)))

    def test_text_arithmetic_is_rejected(self) -> None:
        for op in ArithOp:
            for left, right in (('2', 2), (2, '2'), ('a', 'b')):
                with self.subTest(op=op, left=left, right=right), self.assertRaises(SemanticError):
                    self.evaluate(BinaryOp(op, literal(left), literal(right)))


class TestConditions(EvaluatorCase):
    def test_all_comparisons_for_numbers_and_text(self) -> None:
        cases = (
            (CompOp.EQ, False), (CompOp.NE, True), (CompOp.GT, True),
            (CompOp.GE, True), (CompOp.LT, False), (CompOp.LE, False),
        )
        for left, right in ((3, 2), (3.0, 2), ('b', 'a')):
            for op, expected in cases:
                with self.subTest(op=op, left=left, right=right):
                    result = self.evaluate(compare(literal(left), literal(right), op))
                    self.assertIs(result, expected)

    def test_equal_mixed_numeric_values(self) -> None:
        for op, expected in ((CompOp.EQ, True), (CompOp.NE, False), (CompOp.GT, False),
                             (CompOp.GE, True), (CompOp.LT, False), (CompOp.LE, True)):
            with self.subTest(op=op):
                self.assertIs(self.evaluate(compare(literal(1), literal(1.0), op)), expected)

    def test_mixed_numeric_comparison_keeps_integer_precision(self) -> None:
        left = literal((1 << 53) + 1)
        right = literal(float(1 << 53))
        self.assertTrue(self.evaluator.eval_bool(compare(left, right, CompOp.GT), self.row))
        self.assertFalse(self.evaluator.eval_bool(compare(left, right), self.row))

    def test_column_to_column_comparison_uses_table_prefix(self) -> None:
        expr = compare(ColumnRef('student', 'id'), ColumnRef('course', 'id'), CompOp.LT)
        self.assertTrue(self.evaluator.eval_bool(expr, self.row))

    def test_char_padding_is_ignored_but_varchar_spaces_are_significant(self) -> None:
        for right in ('IF', 'IF ', 'IF   '):
            with self.subTest(right=right):
                self.assertTrue(self.evaluator.eval_bool(compare(ColumnRef('student', 'dept'), literal(right)), self.row))
                self.assertTrue(self.evaluator.eval_bool(compare(literal(right), ColumnRef('student', 'dept')), self.row))
        self.assertFalse(self.evaluator.eval_bool(compare(literal('IF '), literal('IF')), self.row))
        self.assertFalse(self.evaluator.eval_bool(compare(Literal('IF\t', DataType.CHAR), literal('IF')), self.row))

    def test_char_padding_applies_to_all_comparison_operators(self) -> None:
        for op, expected in ((CompOp.EQ, True), (CompOp.NE, False), (CompOp.GT, False),
                             (CompOp.GE, True), (CompOp.LT, False), (CompOp.LE, True)):
            with self.subTest(op=op):
                expr = compare(Literal('IF ', DataType.CHAR), literal('IF  '), op)
                self.assertIs(self.evaluate(expr), expected)

    def test_text_comparison_is_case_sensitive_and_lexicographic(self) -> None:
        self.assertFalse(self.evaluator.eval_bool(compare(literal('Ada'), literal('ada')), self.row))
        self.assertTrue(self.evaluator.eval_bool(compare(literal('10'), literal('2'), CompOp.LT), self.row))
        self.assertTrue(self.evaluator.eval_bool(compare(literal('é'), literal('z'), CompOp.GT), self.row))

    def test_every_comparison_with_null_is_false(self) -> None:
        for op in CompOp:
            for left, right in ((None, 1), (1, None), (None, None), (None, 'text')):
                with self.subTest(op=op, left=left, right=right):
                    self.assertIs(self.evaluate(compare(literal(left), literal(right), op)), False)

    def test_incompatible_comparison_types_are_rejected(self) -> None:
        for left, right in ((1, '1'), ('1', 1.0)):
            for op in CompOp:
                with self.subTest(left=left, right=right, op=op), self.assertRaises(SemanticError):
                    self.evaluate(compare(literal(left), literal(right), op))

    def test_nested_and_and_reused_expression_nodes(self) -> None:
        condition = compare(ColumnRef('student', 'id'), literal(7))
        expr = And([condition, And([condition, compare(ColumnRef('student', 'name'), literal('Ada'))])])
        self.assertTrue(self.evaluator.eval_bool(expr, self.row))

    def test_and_short_circuits_before_runtime_error(self) -> None:
        failing = compare(BinaryOp(ArithOp.DIV, literal(1), literal(0)), literal(1))
        expr = And([compare(literal(1), literal(2)), failing])
        self.assertFalse(self.evaluator.eval_bool(expr, self.row))
        with self.assertRaises(ExecutionError):
            self.evaluator.eval_bool(And([compare(literal(1), literal(1)), failing]), self.row)

    def test_runtime_error_only_happens_for_evaluated_rows(self) -> None:
        condition = compare(BinaryOp(ArithOp.DIV, ColumnRef('student', 'gpa'), literal(0)), literal(1), CompOp.GT)
        rows: list[ExecRow] = []
        self.assertEqual([row for row in rows if self.evaluator.eval_bool(condition, row)], [])
        with self.assertRaises(ExecutionError):
            self.evaluator.eval_bool(condition, self.row)

    def test_conditions_do_not_accept_numeric_or_text_truthiness(self) -> None:
        for value in (0, 1, '', 'text', None):
            with self.subTest(value=value), self.assertRaises(PlanError):
                self.evaluator.eval_bool(literal(value), self.row)
            with self.subTest(and_value=value), self.assertRaises(PlanError):
                self.evaluate(And([literal(value)]))


class TestInvalidExpressions(EvaluatorCase):
    def test_unknown_nodes_operators_and_types_raise_plan_errors(self) -> None:
        for expr in (None, 'id > 1', Expression(), And([]), And(None),
                     BinaryOp('%', literal(1), literal(2)),
                     Comparison('=', literal(1), literal(1)), Literal(1, 'INTEGER')):
            with self.subTest(expr=expr), self.assertRaises(PlanError):
                self.evaluate(expr)

    def test_boolean_and_incompatible_leaf_values_are_rejected(self) -> None:
        for value in (True, False, 1.5, '1'):
            with self.subTest(value=value), self.assertRaises(SemanticError):
                self.evaluate(Literal(value, DataType.INTEGER))
        for data_type, value in ((DataType.FLOAT, True), (DataType.FLOAT, '1'),
                                 (DataType.CHAR, 1), (DataType.VARCHAR, False)):
            with self.subTest(data_type=data_type), self.assertRaises(SemanticError):
                self.evaluate(Literal(value, data_type))
        self.row.values = (True, 3.5, 'IF', 'Ada', 9, 3)
        with self.assertRaises(SemanticError):
            self.evaluate(ColumnRef('student', 'id'))

    def test_nonfinite_and_out_of_range_leaf_values_raise_execution_errors(self) -> None:
        for value in (MIN_INTEGER - 1, MAX_INTEGER + 1, float('nan'), float('inf'), float('-inf')):
            with self.subTest(value=value), self.assertRaises(ExecutionError):
                self.evaluate(literal(value))
        with self.assertRaises(ExecutionError):
            self.evaluate(Literal(10 ** 400, DataType.FLOAT))
        self.row.values = (7, float('nan'), 'IF', 'Ada', 9, 3)
        with self.assertRaises(ExecutionError):
            self.evaluate(ColumnRef('student', 'gpa'))

    def test_malformed_rows_raise_execution_error(self) -> None:
        for row in (None, ExecRow((), self.row.schema), ExecRow((1,), None),
                    ExecRow((1,), OutputSchema(None)),
                    ExecRow([1], OutputSchema([ColumnBinding('t', 'id', DataType.INTEGER)]))):
            with self.subTest(row=row), self.assertRaises(ExecutionError):
                self.evaluator.eval(literal(1), row)

    def test_cycles_and_excessive_depth_raise_plan_error(self) -> None:
        expr = BinaryOp(ArithOp.ADD, literal(1), literal(2))
        expr.left = expr
        with self.assertRaisesRegex(PlanError, 'siklus'):
            self.evaluate(expr)
        expr = And([compare(literal(1), literal(1))])
        expr.conditions.append(expr)
        with self.assertRaisesRegex(PlanError, 'siklus'):
            self.evaluate(expr)
        expr = literal(1)
        for _ in range(1100):
            expr = BinaryOp(ArithOp.ADD, expr, literal(1))
        with self.assertRaisesRegex(PlanError, 'dalam'):
            self.evaluate(expr)

    def test_qp_errors_use_common_base(self) -> None:
        self.assertTrue(issubclass(ExecutionError, MdbmsError))
        self.assertTrue(issubclass(PlanError, MdbmsError))
        with self.assertRaises(MdbmsError) as caught:
            self.evaluate(BinaryOp(ArithOp.DIV, literal(1), literal(0)))
        self.assertEqual(caught.exception.message, 'Pembagian dengan nol')


if __name__ == '__main__':
    unittest.main()
