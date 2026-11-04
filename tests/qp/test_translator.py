"""Uji translasi dan validasi plan QP-03."""

from __future__ import annotations

import copy
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import create_autospec, patch

from common.catalog import ColumnSchema, DataType, IndexInfo, IndexKind, TableSchema
from common.errors import MdbmsError, SemanticError
from common.expression import And, ArithOp, BinaryOp, ColumnRef, Comparison, CompOp, Literal
from common.interfaces import ConcurrencyControlManager, StorageManager
from common.plan import (
    CreateIndex, Delete, Filter, IndexScan, Insert, Join, JoinKind, ParsedQuery,
    PlanNode, Project, QueryKind, Sort, TableScan, TransactionCommand, Update,
)
from common.transaction import Action
from query_processor.context import ExecContext
from query_processor.errors import PlanError
from query_processor.operators.create_index import CreateIndexExecution
from query_processor.operators.delete import DeleteExecution
from query_processor.operators.filter import FilterOp
from query_processor.operators.insert import InsertExecution
from query_processor.operators.join import BlockNestedLoopJoinOp
from query_processor.operators.project import ProjectOp, SelectExecution
from query_processor.operators.scan import IndexScanOp, TableScanOp
from query_processor.operators.sort import SortOp
from query_processor.operators.update import UpdateExecution
from query_processor.translator import PlanTranslator
from query_processor.tuple import ColumnBinding, OutputSchema


def ref(table: str | None, name: str = 'id') -> ColumnRef:
    return ColumnRef(table, name)


def integer(value: int = 1) -> Literal:
    return Literal(value, DataType.INTEGER)


def predicate(table: str = 'student') -> Comparison:
    return Comparison(CompOp.GE, ref(table), integer())


def scan(table: str = 'student') -> TableScan:
    return TableScan(table=table)


class TranslatorCase(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = {
            'student': TableSchema('student', [
                ColumnSchema('id', DataType.INTEGER),
                ColumnSchema('name', DataType.VARCHAR, 32),
                ColumnSchema('gpa', DataType.FLOAT),
            ]),
            'course': TableSchema('course', [
                ColumnSchema('id', DataType.INTEGER),
                ColumnSchema('title', DataType.VARCHAR, 32),
            ]),
            'enroll': TableSchema('enroll', [
                ColumnSchema('id', DataType.INTEGER),
                ColumnSchema('grade', DataType.CHAR, 2),
            ]),
        }
        self.sm = create_autospec(StorageManager, instance=True, spec_set=True)
        self.ccm = create_autospec(ConcurrencyControlManager, instance=True, spec_set=True)

        def get_schema(table: str) -> TableSchema:
            if table not in self.catalog:
                raise SemanticError(f"Tabel '{table}' tidak ada", table)
            return copy.deepcopy(self.catalog[table])

        self.sm.get_schema.side_effect = get_schema
        self.ctx = ExecContext(17, self.sm, self.ccm)
        self.translator = PlanTranslator()

    def build(self, kind: QueryKind, node: PlanNode):
        return self.translator.build(ParsedQuery('query buatan tangan', kind, node), self.ctx)

    def rows(self, node: PlanNode):
        return self.translator.build_rows(node, self.ctx)

    def tearDown(self) -> None:
        # Build hanya boleh membaca katalog.
        self.assertTrue(all(call[0] == 'get_schema' for call in self.sm.mock_calls))
        self.assertEqual(self.ccm.mock_calls, [])


class TestPlanMapping(TranslatorCase):
    def test_select_keeps_tree_order_parameters_and_plan(self) -> None:
        condition = And([predicate(), Comparison(CompOp.LT, ref('student', 'gpa'),
                                                Literal(4.0, DataType.FLOAT))])
        plan = Project(columns=[ref('student', 'name'), ref('student')], children=[
            Sort(column=ref('student', 'gpa'), ascending=False, children=[
                Filter(condition=condition, children=[scan()]),
            ]),
        ], estimated_cost=12.5)
        before = copy.deepcopy(plan)
        result = self.build(QueryKind.SELECT, plan)
        self.assertIsInstance(result, SelectExecution)
        project = result._root
        self.assertIsInstance(project, ProjectOp)
        self.assertEqual([c.name for c in project.schema.columns], ['name', 'id'])
        self.assertEqual(project._columns, plan.columns)
        sort = project._child
        self.assertIsInstance(sort, SortOp)
        self.assertEqual(sort._column, ref('student', 'gpa'))
        self.assertFalse(sort._ascending)
        self.assertIsInstance(sort._child, FilterOp)
        self.assertIs(sort._child._condition, condition)
        self.assertIsInstance(sort._child._child, TableScanOp)
        self.assertEqual(sort._child._child._table, 'student')
        self.assertEqual(plan, before)
        self.sm.get_schema.assert_called_once_with('student')

    def test_table_scan_has_schema_before_open(self) -> None:
        result = self.rows(scan())
        self.assertIsInstance(result, TableScanOp)
        self.assertEqual([(c.qualifier, c.name, c.type) for c in result.schema.columns], [
            ('student', 'id', DataType.INTEGER), ('student', 'name', DataType.VARCHAR),
            ('student', 'gpa', DataType.FLOAT),
        ])

    def test_index_scan_forwards_equality_and_all_range_operators(self) -> None:
        index = IndexInfo('idx_id', 'student', 'id')
        for op, params in (
            (CompOp.EQ, {'key': 3}), (CompOp.GT, {'low': 3}),
            (CompOp.GE, {'low': 3, 'high': 9}), (CompOp.LT, {'high': 9}),
            (CompOp.LE, {'high': 9}),
        ):
            with self.subTest(op=op):
                result = self.rows(IndexScan(table='student', index=index, op=op, **params))
                self.assertIsInstance(result, IndexScanOp)
                self.assertIs(result._index, index)
                self.assertIs(result._op, op)
                self.assertEqual((result._key, result._low, result._high),
                                 (params.get('key'), params.get('low'), params.get('high')))

    def test_equality_index_accepts_null_key_without_evaluating_it(self) -> None:
        result = self.rows(IndexScan(table='student', index=IndexInfo('i', 'student', 'id'),
                                     op=CompOp.EQ, key=None))
        self.assertIsNone(result._key)

    def test_cross_and_theta_join_preserve_left_right(self) -> None:
        condition = Comparison(CompOp.EQ, ref('student'), ref('course'))
        for kind, expr in ((JoinKind.CROSS, None), (JoinKind.THETA, condition)):
            with self.subTest(kind=kind):
                result = self.rows(Join(kind=kind, condition=expr, children=[scan(), scan('course')]))
                self.assertIsInstance(result, BlockNestedLoopJoinOp)
                self.assertEqual(result._left._table, 'student')
                self.assertEqual(result._right._table, 'course')
                self.assertIs(result._join_kind, kind)
                self.assertIs(result._condition, expr)
                self.assertEqual(result._shared_columns, ())
                self.assertEqual(result.schema.index_of(ref('course')), 3)
                self.assertEqual(len(result.schema.columns), 5)

    def test_natural_join_deduplicates_and_resolves_both_prefixes(self) -> None:
        plan = Join(kind=JoinKind.NATURAL, children=[scan(), scan('course')])
        result = self.rows(plan)
        self.assertEqual(result._shared_columns, ((0, 0),))
        self.assertEqual([c.name for c in result.schema.columns], ['id', 'name', 'gpa', 'title'])
        for table in (None, 'student', 'course'):
            self.assertEqual(result.schema.index_of(ref(table)), 0)
        # Skema child tetap utuh.
        self.assertEqual(result._left.schema.columns[0].alternate_qualifiers, ())

    def test_nested_natural_join_and_projection_preserve_shared_origins(self) -> None:
        inner = Join(kind=JoinKind.NATURAL, children=[scan(), scan('course')])
        outer = Join(kind=JoinKind.NATURAL, children=[inner, scan('enroll')])
        result = self.rows(Project(columns=[ref('enroll'), ref('course', 'title')], children=[outer]))
        self.assertEqual([c.name for c in result.schema.columns], ['id', 'title'])
        for table in ('student', 'course', 'enroll'):
            self.assertEqual(result.schema.index_of(ref(table)), 0)
        self.assertEqual(result._child._shared_columns, ((0, 0),))

    def test_natural_join_without_shared_columns_keeps_cartesian_schema(self) -> None:
        left = Project(columns=[ref('student', 'name')], children=[scan()])
        result = self.rows(Join(kind=JoinKind.NATURAL, children=[left, scan('course')]))
        self.assertEqual(result._shared_columns, ())
        self.assertEqual([c.name for c in result.schema.columns], ['name', 'id', 'title'])

    def test_projection_pushdown_exposes_schema_to_filter_join_and_sort(self) -> None:
        left = Project(columns=[ref('student'), ref('student', 'name')], children=[scan()])
        right = Project(columns=[ref('course')], children=[scan('course')])
        join = Join(kind=JoinKind.THETA, condition=Comparison(CompOp.EQ, ref('student'), ref('course')),
                    children=[left, right])
        result = self.rows(Sort(column=ref('student', 'name'), children=[
            Filter(condition=predicate('course'), children=[join]),
        ]))
        self.assertEqual([c.name for c in result.schema.columns], ['id', 'name', 'id'])
        self.assertEqual(result.schema.index_of(ref('course')), 2)

    def test_insert_extracts_values_without_evaluator(self) -> None:
        values = [integer(5), Literal('A;b', DataType.VARCHAR), Literal(None, DataType.FLOAT)]
        result = self.build(QueryKind.INSERT, Insert(table='student', values=values))
        self.assertIsInstance(result, InsertExecution)
        self.assertEqual(result._table, 'student')
        self.assertEqual(result._values, (5, 'A;b', None))

    def test_update_preserves_expression_for_runtime(self) -> None:
        expression = BinaryOp(ArithOp.ADD, ref('student', 'gpa'), Literal(0.2, DataType.FLOAT))
        result = self.build(QueryKind.UPDATE, Update(table='student', column='gpa',
                            expression=expression, children=[Filter(condition=predicate(), children=[scan()])]))
        self.assertIsInstance(result, UpdateExecution)
        self.assertEqual((result._table, result._column), ('student', 'gpa'))
        self.assertIs(result._value_expr, expression)
        self.assertIsInstance(result._source, FilterOp)

    def test_delete_supports_filtered_index_source(self) -> None:
        source = IndexScan(table='student', index=IndexInfo('i', 'student', 'id'), op=CompOp.EQ, key=3)
        result = self.build(QueryKind.DELETE, Delete(table='student', children=[
            Filter(condition=predicate(), children=[source]),
        ]))
        self.assertIsInstance(result, DeleteExecution)
        self.assertEqual(result._table, 'student')
        self.assertIsInstance(result._source._child, IndexScanOp)

    def test_create_index_forwards_metadata(self) -> None:
        for kind in IndexKind:
            with self.subTest(kind=kind):
                index = IndexInfo('i', 'student', 'id', kind)
                result = self.build(QueryKind.CREATE_INDEX, CreateIndex(index=index))
                self.assertIsInstance(result, CreateIndexExecution)
                self.assertIs(result._index, index)

    def test_build_does_not_open_or_evaluate(self) -> None:
        zero_division = BinaryOp(ArithOp.DIV, ref('student', 'gpa'), integer(0))
        plan = Project(columns=[ref('student')], children=[
            Filter(condition=Comparison(CompOp.GT, zero_division, integer(1)), children=[scan()]),
        ])
        with patch.object(TableScanOp, 'open', side_effect=AssertionError('open saat build')), \
             patch('query_processor.evaluator.ExpressionEvaluator.eval', side_effect=AssertionError('eval saat build')):
            self.build(QueryKind.SELECT, plan)

    def test_each_build_has_independent_operators_and_fresh_catalog(self) -> None:
        first = self.rows(scan())
        first.schema.columns.clear()
        second = self.rows(scan())
        self.assertEqual(len(second.schema.columns), 3)
        self.assertEqual(len(self.catalog['student'].columns), 3)
        self.assertEqual(self.sm.get_schema.call_count, 2)

    def test_translator_can_be_shared_between_sessions(self) -> None:
        def build_for(table: str):
            ctx = ExecContext(18, self.sm, self.ccm, Action.WRITE)
            result = self.translator.build_rows(scan(table), ctx)
            self.assertIs(ctx.access_intent, Action.WRITE)
            return result.schema.columns[0].qualifier

        tables = ['student', 'course', 'enroll'] * 4
        with ThreadPoolExecutor(max_workers=3) as pool:
            self.assertEqual(list(pool.map(build_for, tables)), tables)


class TestInvalidPlans(TranslatorCase):
    def test_unknown_nodes_and_wrong_arity_fail_before_catalog(self) -> None:
        for node in (None, object(), PlanNode(), TableScan(table='student', children=[scan()]),
                     Project(columns=[ref('student')]), Filter(condition=predicate(), children=[scan(), scan()]),
                     Join(kind=JoinKind.CROSS, children=[scan()]),
                     CreateIndex(index=IndexInfo('i', 'student', 'id'), children=[scan()])):
            with self.subTest(node=type(node).__name__), self.assertRaises(PlanError):
                self.rows(node)
        self.assertEqual(self.sm.mock_calls, [])

    def test_bad_children_and_nested_commands_are_rejected(self) -> None:
        for children in (None, (scan(),), [None], [Insert(table='student', values=[integer()])]):
            with self.subTest(children=children), self.assertRaises(PlanError):
                self.rows(Project(columns=[ref('student')], children=children))

    def test_plan_cycle_is_rejected(self) -> None:
        node = Filter(condition=predicate())
        node.children = [node]
        with self.assertRaisesRegex(PlanError, 'siklus'):
            self.rows(node)

    def test_expression_cycle_and_excessive_depth_are_rejected(self) -> None:
        expr = BinaryOp(ArithOp.ADD, integer(), integer())
        expr.left = expr
        with self.assertRaisesRegex(PlanError, 'siklus'):
            self.translator.validate(Update(table='student', column='id', expression=expr, children=[scan()]))
        deep = scan()
        for _ in range(1100):
            deep = Filter(condition=predicate(), children=[deep])
        with self.assertRaisesRegex(PlanError, 'terlalu dalam'):
            self.translator.validate(deep)

    def test_empty_parameters_wildcards_and_invalid_expressions_are_rejected(self) -> None:
        cases = [
            TableScan(table=' '), Project(columns=[], children=[scan()]),
            Project(columns=[ref('student', '*')], children=[scan()]),
            Project(columns=['id'], children=[scan()]),
            Filter(condition=None, children=[scan()]), Filter(condition='id > 1', children=[scan()]),
            Filter(condition=And([]), children=[scan()]),
            Filter(condition=Comparison('=', ref('student'), integer()), children=[scan()]),
            Filter(condition=Comparison(CompOp.EQ, None, integer()), children=[scan()]),
            Update(table='student', column='id', expression=BinaryOp('%', integer(), integer()), children=[scan()]),
            Sort(column=ref('student'), ascending='DESC', children=[scan()]),
            Insert(table='student', values=[]), Insert(table='student', values=[1]),
            CreateIndex(index=None), CreateIndex(index=IndexInfo('', 'student', 'id')),
        ]
        for node in cases:
            with self.subTest(node=type(node).__name__), self.assertRaises(PlanError):
                self.translator.validate(node)

    def test_unknown_literals_and_nonfinite_values_are_rejected(self) -> None:
        for value in (Literal(True, DataType.INTEGER), Literal(float('inf'), DataType.FLOAT),
                      Literal(float('nan'), DataType.FLOAT), Literal(object(), DataType.INTEGER),
                      Literal(1, 'INTEGER')):
            with self.subTest(value=value), self.assertRaises(PlanError):
                self.translator.validate(Insert(table='student', values=[value]))

    def test_root_kind_mismatch_missing_plan_and_facade_commands_are_rejected(self) -> None:
        for parsed in (None, ParsedQuery('', 'SELECT', scan()), ParsedQuery('', QueryKind.SELECT),
                       ParsedQuery('', QueryKind.SELECT, scan()),
                       ParsedQuery('', QueryKind.INSERT, Project(columns=[ref('student')], children=[scan()])),
                       ParsedQuery('', QueryKind.TRANSACTION, command=TransactionCommand.BEGIN),
                       ParsedQuery('', QueryKind.EXPLAIN, scan()),
                       ParsedQuery('', QueryKind.DELETE, Delete(table='student', children=[scan()]),
                                   command=TransactionCommand.ABORT)):
            with self.subTest(parsed=parsed), self.assertRaises(PlanError):
                self.translator.build(parsed, self.ctx)

    def test_commands_cannot_be_built_as_row_subtrees(self) -> None:
        with self.assertRaisesRegex(PlanError, 'subtree baris'):
            self.rows(Insert(table='student', values=[integer()]))

    def test_unsupported_or_inconsistent_join_parameters_are_rejected(self) -> None:
        for params in ({'kind': 'CROSS'}, {'kind': JoinKind.CROSS, 'algorithm': 'HASH'},
                       {'kind': JoinKind.THETA}, {'kind': JoinKind.CROSS, 'condition': predicate()},
                       {'kind': JoinKind.NATURAL, 'condition': predicate()}):
            with self.subTest(params=params), self.assertRaises(PlanError):
                self.rows(Join(children=[scan(), scan('course')], **params))

    def test_index_mismatch_unsupported_search_and_invalid_bounds_are_rejected(self) -> None:
        index = IndexInfo('i', 'student', 'id')
        cases = [
            {'index': IndexInfo('i', 'course', 'id'), 'op': CompOp.EQ},
            {'index': IndexInfo('i', 'student', 'id', IndexKind.HASH), 'op': CompOp.GE, 'low': 1},
            {'index': index, 'op': CompOp.NE, 'key': 1}, {'index': index, 'op': '='},
            {'index': index, 'op': CompOp.EQ, 'low': 1},
            {'index': index, 'op': CompOp.GT}, {'index': index, 'op': CompOp.LT},
            {'index': index, 'op': CompOp.GE, 'low': 1, 'key': 1},
            {'index': index, 'op': CompOp.EQ, 'key': integer()},
        ]
        for params in cases:
            with self.subTest(params=params), self.assertRaises(PlanError):
                self.rows(IndexScan(table='student', **params))

    def test_missing_or_ambiguous_columns_in_all_expression_positions(self) -> None:
        missing = ref('student', 'missing')
        for node in (
            Project(columns=[missing], children=[scan()]), Sort(column=missing, children=[scan()]),
            Filter(condition=And([Comparison(CompOp.GT, BinaryOp(ArithOp.ADD, missing, integer()), integer())]),
                   children=[scan()]),
            Join(kind=JoinKind.THETA, condition=Comparison(CompOp.EQ, missing, ref('course')),
                 children=[scan(), scan('course')]),
            Project(columns=[ref(None)], children=[Join(kind=JoinKind.CROSS, children=[scan(), scan('course')])]),
        ):
            with self.subTest(node=type(node).__name__), self.assertRaisesRegex(PlanError, 'kolom'):
                self.rows(node)

    def test_parent_cannot_use_column_removed_by_projection(self) -> None:
        with self.assertRaisesRegex(PlanError, 'gpa'):
            self.rows(Sort(column=ref('student', 'gpa'), children=[
                Project(columns=[ref('student')], children=[scan()]),
            ]))

    def test_mutation_sources_must_preserve_target_record_identity(self) -> None:
        for source in (scan('course'), Project(columns=[ref('student')], children=[scan()]),
                       Join(kind=JoinKind.CROSS, children=[scan(), scan('course')])):
            for cls in (Update, Delete):
                params = {'column': 'id', 'expression': integer()} if cls is Update else {}
                with self.subTest(source=type(source).__name__, command=cls.__name__), self.assertRaises(PlanError):
                    self.translator.validate(cls(table='student', children=[source], **params))

    def test_update_target_and_value_references_are_checked(self) -> None:
        for column, expr in (('missing', integer()), ('gpa', ref('course'))):
            with self.subTest(column=column), self.assertRaises(PlanError):
                self.build(QueryKind.UPDATE, Update(table='student', column=column, expression=expr, children=[scan()]))

    def test_insert_count_and_index_column_must_match_catalog(self) -> None:
        cases = [
            (QueryKind.INSERT, Insert(table='student', values=[integer()])),
            (QueryKind.CREATE_INDEX, CreateIndex(index=IndexInfo('i', 'student', 'missing'))),
            (QueryKind.SELECT, Project(columns=[ref('student')], children=[
                IndexScan(table='student', index=IndexInfo('i', 'student', 'missing'), op=CompOp.EQ, key=1),
            ])),
        ]
        for kind, node in cases:
            with self.subTest(kind=kind), self.assertRaises(PlanError):
                self.build(kind, node)

    def test_missing_table_and_empty_catalog_schema_report_plan_errors(self) -> None:
        with self.assertRaisesRegex(PlanError, 'missing'):
            self.rows(scan('missing'))
        self.catalog['empty_schema'] = TableSchema('empty_schema', [])
        with self.assertRaisesRegex(PlanError, 'skema'):
            self.rows(scan('empty_schema'))

    def test_natural_join_rejects_incompatible_shared_types_and_ambiguity(self) -> None:
        self.catalog['course'].columns[0].type = DataType.VARCHAR
        with self.assertRaisesRegex(PlanError, 'tidak kompatibel'):
            self.rows(Join(kind=JoinKind.NATURAL, children=[scan(), scan('course')]))
        self.catalog['course'].columns[0].type = DataType.INTEGER
        cross = Join(kind=JoinKind.CROSS, children=[scan(), scan('course')])
        for children in ([cross, scan('enroll')], [scan('enroll'), cross]):
            with self.subTest(side=children[0].__class__.__name__), self.assertRaisesRegex(PlanError, 'ambigu'):
                self.rows(Join(kind=JoinKind.NATURAL, children=children))


class TestOutputSchema(unittest.TestCase):
    def test_qualified_unqualified_and_shared_origin_resolution(self) -> None:
        schema = OutputSchema([
            ColumnBinding('student', 'id', DataType.INTEGER, ('course',)),
            ColumnBinding('student', 'name', DataType.VARCHAR),
        ])
        for table in (None, 'student', 'course'):
            self.assertEqual(schema.index_of(ref(table)), 0)
        self.assertEqual(schema.index_of(ref(None, 'name')), 1)

    def test_missing_ambiguous_and_invalid_references_raise_common_error(self) -> None:
        schema = OutputSchema([ColumnBinding('student', 'id', DataType.INTEGER),
                               ColumnBinding('course', 'id', DataType.INTEGER)])
        for column in (ref(None), ref('missing'), 'id'):
            with self.subTest(column=column), self.assertRaises(MdbmsError):
                schema.index_of(column)
        self.assertTrue(issubclass(PlanError, MdbmsError))

    def test_projection_reordering_and_repeated_columns_are_preserved(self) -> None:
        schema = OutputSchema([ColumnBinding('student', 'name', DataType.VARCHAR),
                               ColumnBinding('student', 'id', DataType.INTEGER),
                               ColumnBinding('student', 'id', DataType.INTEGER)])
        self.assertEqual(schema.index_of(ref('student', 'name')), 0)
        with self.assertRaisesRegex(PlanError, 'ambigu'):
            schema.index_of(ref('student'))


if __name__ == '__main__':
    unittest.main()
