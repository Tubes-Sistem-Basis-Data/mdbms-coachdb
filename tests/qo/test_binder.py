"""QO-07: binding plan terhadap kontrak katalog, tanpa akses data SM."""

from __future__ import annotations

import copy
import unittest
from unittest.mock import create_autospec

from common.catalog import ColumnSchema, DataType, IndexInfo, IndexKind, TableSchema
from common.errors import SemanticError, StorageError
from common.expression import And, ArithOp, BinaryOp, ColumnRef, Comparison, CompOp, Literal
from common.interfaces import StorageManager
from common.plan import (
    CreateIndex, Delete, Filter, IndexScan, Insert, Join, JoinKind, ParsedQuery,
    PlanNode, Project, QueryKind, Sort, TableScan, TransactionCommand, Update,
)
from query_optimizer.binder import Binder, bind_query
from query_optimizer.expression_parser import ExpressionParser
from query_optimizer.lexer import Lexer
from query_optimizer.select_parser import SelectParser


class BinderCase(unittest.TestCase):
    def setUp(self) -> None:
        self.schemas = {
            "student": TableSchema("student", [
                ColumnSchema("id", DataType.INTEGER, nullable=False),
                ColumnSchema("name", DataType.VARCHAR, length=10),
                ColumnSchema("gpa", DataType.FLOAT),
            ]),
            "course": TableSchema("course", [
                ColumnSchema("id", DataType.INTEGER),
                ColumnSchema("title", DataType.VARCHAR, length=30),
            ]),
            "enrollment": TableSchema("enrollment", [
                ColumnSchema("student_id", DataType.INTEGER),
                ColumnSchema("course_id", DataType.INTEGER),
            ]),
            "profile": TableSchema("profile", [
                ColumnSchema("id", DataType.INTEGER),
                ColumnSchema("city", DataType.VARCHAR, length=20),
            ]),
        }
        self.sm = create_autospec(StorageManager, instance=True, spec_set=True)

        def get_schema(table: str) -> TableSchema:
            if table not in self.schemas:
                raise SemanticError(f"Tabel '{table}' tidak ada", table)
            return self.schemas[table]

        self.sm.get_schema.side_effect = get_schema
        self.binder = Binder(self.sm)

    def tearDown(self) -> None:
        # Binder hanya membaca skema lewat facade, tanpa scan/statistik/tulis.
        self.assertTrue(all(call[0] == "get_schema" for call in self.sm.mock_calls))

    def parse(self, sql: str) -> ParsedQuery:
        query = SelectParser(ExpressionParser()).parse_select(Lexer().tokenize(sql))
        query.sql = sql
        return query

    def bind_sql(self, sql: str) -> ParsedQuery:
        return self.binder.bind(self.parse(sql))

    def bind_node(self, node: PlanNode, kind: QueryKind = QueryKind.SELECT) -> ParsedQuery:
        return self.binder.bind(ParsedQuery("plan langsung", kind, plan=node))


class TestSelectBinding(BinderCase):
    def test_star_expands_in_catalog_order_without_mutating_catalog(self) -> None:
        before = copy.deepcopy(self.schemas)
        query = self.parse("SELECT * FROM student")
        self.assertIs(self.binder.bind(query), query)
        expected = [ColumnRef("student", name) for name in ("id", "name", "gpa")]
        self.assertEqual(query.plan.columns, expected)
        self.assertEqual(query.plan.output_columns, expected)
        self.assertEqual(query.plan.children[0].output_columns, expected)
        self.assertEqual(self.schemas, before)

    def test_projection_preserves_order_and_duplicates(self) -> None:
        query = self.bind_sql("SELECT name, student.id, name FROM student")
        expected = [ColumnRef("student", name) for name in ("name", "id", "name")]
        self.assertEqual(query.plan.columns, expected)
        self.assertEqual(query.plan.output_columns, expected)

    def test_where_arithmetic_and_order_by_are_bound(self) -> None:
        query = self.bind_sql(
            "SELECT name FROM student WHERE gpa + 1 >= 3 AND id <> 0 ORDER BY id DESC"
        )
        sort = query.plan.children[0]
        filt = sort.children[0]
        self.assertEqual(sort.column, ColumnRef("student", "id"))
        self.assertFalse(sort.ascending)
        self.assertEqual(filt.condition.conditions[0].left.left, ColumnRef("student", "gpa"))
        self.assertEqual(filt.condition.conditions[1].left, ColumnRef("student", "id"))
        self.assertEqual(sort.output_columns, filt.output_columns)
        self.assertEqual(len(filt.output_columns), 3)

    def test_unknown_table_preserves_catalog_error(self) -> None:
        with self.assertRaises(SemanticError) as caught:
            self.bind_sql("SELECT * FROM missing")
        self.assertEqual(caught.exception.object_name, "missing")

    def test_unknown_column_in_each_clause_is_rejected(self) -> None:
        for sql in (
            "SELECT missing FROM student", "SELECT student.missing FROM student",
            "SELECT name FROM student WHERE missing = 1",
            "SELECT name FROM student ORDER BY missing",
            "SELECT name FROM student WHERE missing + 1 = 2",
        ):
            with self.subTest(sql=sql), self.assertRaises(SemanticError):
                self.bind_sql(sql)

    def test_qualified_column_cannot_reference_table_outside_from(self) -> None:
        with self.assertRaises(SemanticError):
            self.bind_sql("SELECT course.id FROM student")

    def test_unique_unqualified_column_in_join_is_resolved(self) -> None:
        query = self.bind_sql(
            "SELECT name, student_id FROM student JOIN enrollment ON id = student_id"
        )
        self.assertEqual(query.plan.columns, [
            ColumnRef("student", "name"), ColumnRef("enrollment", "student_id"),
        ])
        join = query.plan.children[0]
        self.assertEqual(join.condition.left, ColumnRef("student", "id"))
        self.assertEqual(join.condition.right, ColumnRef("enrollment", "student_id"))
        self.assertEqual(len(join.output_columns), 5)

    def test_ambiguous_column_is_rejected(self) -> None:
        for sql in (
            "SELECT id FROM student, course",
            "SELECT name FROM student, course WHERE id = 1",
            "SELECT name FROM student, course ORDER BY id",
            "SELECT name FROM student JOIN course ON id = course.id",
        ):
            with self.subTest(sql=sql), self.assertRaisesRegex(SemanticError, "ambigu"):
                self.bind_sql(sql)

    def test_join_on_cannot_see_table_in_later_join(self) -> None:
        with self.assertRaises(SemanticError):
            self.bind_sql(
                "SELECT * FROM student JOIN course ON profile.id = student.id, profile"
            )

    def test_repeated_table_without_alias_is_rejected(self) -> None:
        with self.assertRaises(SemanticError):
            self.bind_sql("SELECT * FROM student, student")

    def test_projection_does_not_hide_repeated_table_provenance(self) -> None:
        projected = Project(columns=[ColumnRef("course", "title")], children=[
            Join(kind=JoinKind.CROSS, children=[TableScan(table="student"), TableScan(table="course")]),
        ])
        node = Join(kind=JoinKind.CROSS, children=[projected, TableScan(table="student")])
        with self.assertRaisesRegex(SemanticError, "Tabel berulang"):
            self.bind_node(node)

    def test_column_removed_by_child_project_is_out_of_scope(self) -> None:
        child = Project(columns=[ColumnRef(None, "name")], children=[TableScan(table="student")])
        node = Sort(column=ColumnRef("student", "id"), children=[child])
        with self.assertRaises(SemanticError):
            self.bind_node(node)

    def test_comparison_accepts_numeric_and_text_families(self) -> None:
        self.bind_sql("SELECT id FROM student WHERE id = gpa AND name = 'Ada'")

    def test_incompatible_comparison_and_arithmetic_are_rejected(self) -> None:
        for condition in ("name = id", "name + 1 = 2", "id = 'Ada'"):
            with self.subTest(condition=condition), self.assertRaises(SemanticError):
                self.bind_sql(f"SELECT id FROM student WHERE {condition}")

    def test_condition_must_be_boolean(self) -> None:
        for condition in (ColumnRef(None, "id"), And([Literal(1, DataType.INTEGER)])):
            with self.subTest(condition=condition), self.assertRaises(SemanticError):
                self.bind_node(Filter(condition=condition, children=[TableScan(table="student")]))

    def test_storage_failure_is_not_hidden_as_semantic_error(self) -> None:
        self.sm.get_schema.side_effect = StorageError("catalog rusak")
        with self.assertRaises(StorageError):
            self.bind_sql("SELECT * FROM student")

    def test_semantic_error_subclass_from_storage_is_preserved(self) -> None:
        class MissingTable(SemanticError):
            pass

        error = MissingTable("tidak ada", "missing")
        self.sm.get_schema.side_effect = error
        with self.assertRaises(MissingTable) as caught:
            self.bind_sql("SELECT * FROM missing")
        self.assertIs(caught.exception, error)

    def test_catalog_is_refreshed_between_bind_calls(self) -> None:
        self.bind_sql("SELECT name FROM student")
        self.schemas["student"].columns = [ColumnSchema("id", DataType.INTEGER)]
        with self.assertRaises(SemanticError):
            self.bind_sql("SELECT name FROM student")

    def test_binding_twice_is_idempotent(self) -> None:
        query = self.bind_sql("SELECT * FROM student WHERE id = 1")
        before = copy.deepcopy(query)
        self.binder.bind(query)
        self.assertEqual(query, before)

    def test_data_query_without_catalog_fails_explicitly(self) -> None:
        with self.assertRaises(SemanticError):
            Binder().bind(self.parse("SELECT * FROM student"))

    def test_transaction_does_not_need_catalog(self) -> None:
        query = ParsedQuery("BEGIN", QueryKind.TRANSACTION, command=TransactionCommand.BEGIN)
        self.assertIs(Binder().bind(query), query)

    def test_data_query_without_plan_is_rejected(self) -> None:
        with self.assertRaises(SemanticError):
            self.binder.bind(ParsedQuery("SELECT", QueryKind.SELECT))

    def test_explain_plan_is_bound(self) -> None:
        query = self.parse("SELECT name FROM student")
        query.kind = QueryKind.EXPLAIN
        self.binder.bind(query)
        self.assertEqual(query.plan.columns, [ColumnRef("student", "name")])

    def test_unknown_node_and_wrong_arity_fail_explicitly(self) -> None:
        for node in (PlanNode(), Project(columns=[]), Join(kind=JoinKind.CROSS)):
            with self.subTest(node=node), self.assertRaises(SemanticError):
                self.bind_node(node)

    def test_plan_and_expression_cycles_fail_explicitly(self) -> None:
        project = Project(columns=[])
        project.children = [project]
        expr = BinaryOp(ArithOp.ADD, Literal(1, DataType.INTEGER), Literal(2, DataType.INTEGER))
        expr.left = expr
        for node in (project, Filter(
            condition=Comparison(CompOp.EQ, expr, Literal(1, DataType.INTEGER)),
            children=[TableScan(table="student")],
        )):
            with self.subTest(node=type(node).__name__), self.assertRaises(SemanticError):
                self.bind_node(node)


class TestNaturalJoinBinding(BinderCase):
    def test_star_merges_shared_columns_in_left_then_right_order(self) -> None:
        query = self.bind_sql("SELECT * FROM student NATURAL JOIN course")
        self.assertEqual(query.plan.columns, [
            ColumnRef("student", "id"), ColumnRef("student", "name"),
            ColumnRef("student", "gpa"), ColumnRef("course", "title"),
        ])

    def test_shared_column_unqualified_and_right_qualified_are_canonicalized(self) -> None:
        query = self.bind_sql(
            "SELECT id, course.id FROM student NATURAL JOIN course WHERE course.id = 1"
        )
        self.assertEqual(query.plan.columns, [ColumnRef("student", "id")] * 2)
        self.assertEqual(query.plan.children[0].condition.left, ColumnRef("student", "id"))

    def test_nested_natural_join_keeps_all_qualifiers(self) -> None:
        query = self.bind_sql(
            "SELECT profile.id, course.id FROM student NATURAL JOIN course NATURAL JOIN profile"
        )
        self.assertEqual(query.plan.columns, [ColumnRef("student", "id")] * 2)
        self.assertEqual(len(query.plan.children[0].output_columns), 5)

    def test_no_shared_columns_behaves_as_cross_join(self) -> None:
        query = self.bind_sql("SELECT * FROM student NATURAL JOIN enrollment")
        self.assertEqual(len(query.plan.columns), 5)

    def test_incompatible_shared_column_types_are_rejected(self) -> None:
        self.schemas["course"].columns[0].type = DataType.VARCHAR
        with self.assertRaises(SemanticError):
            self.bind_sql("SELECT * FROM student NATURAL JOIN course")

    def test_ambiguous_shared_column_on_left_is_rejected(self) -> None:
        cross = Join(kind=JoinKind.CROSS, children=[TableScan(table="student"), TableScan(table="course")])
        natural = Join(kind=JoinKind.NATURAL, children=[cross, TableScan(table="profile")])
        with self.assertRaises(SemanticError):
            self.bind_node(Project(columns=[], children=[natural]))


class TestWriteBinding(BinderCase):
    def values(self) -> list[Literal]:
        return [Literal(1, DataType.INTEGER), Literal("Ada", DataType.VARCHAR), Literal(4, DataType.INTEGER)]

    def test_insert_validates_full_row_and_allows_integer_into_float(self) -> None:
        node = Insert(table="student", values=self.values())
        self.bind_node(node, QueryKind.INSERT)
        self.assertEqual(node.output_columns, [])
        self.assertEqual(self.sm.get_schema.call_count, 1)

    def test_insert_missing_table_and_wrong_value_counts_are_rejected(self) -> None:
        for table, values in (("missing", self.values()), ("student", []), ("student", self.values()[:2])):
            with self.subTest(table=table, values=values), self.assertRaises(SemanticError):
                self.bind_node(Insert(table=table, values=values), QueryKind.INSERT)

    def test_insert_incompatible_types_and_oversized_text_are_rejected(self) -> None:
        for pos, value in (
            (0, Literal(1.5, DataType.FLOAT)), (0, Literal("1", DataType.VARCHAR)),
            (1, Literal("abcdefghijk", DataType.VARCHAR)), (2, Literal("A", DataType.CHAR)),
        ):
            values = self.values()
            values[pos] = value
            with self.subTest(pos=pos, value=value), self.assertRaises(SemanticError):
                self.bind_node(Insert(table="student", values=values), QueryKind.INSERT)

    def test_insert_null_respects_nullable_even_with_different_literal_type(self) -> None:
        values = self.values()
        values[1] = Literal(None, DataType.INTEGER)
        self.bind_node(Insert(table="student", values=values), QueryKind.INSERT)
        values[0] = Literal(None, DataType.INTEGER)
        with self.assertRaises(SemanticError):
            self.bind_node(Insert(table="student", values=values), QueryKind.INSERT)

    def test_insert_rejects_nonliteral_values(self) -> None:
        values = self.values()
        values[0] = ColumnRef("student", "id")
        with self.assertRaises(SemanticError):
            self.bind_node(Insert(table="student", values=values), QueryKind.INSERT)

    def test_literal_value_must_match_declared_type(self) -> None:
        for value in (Literal(True, DataType.INTEGER), Literal("1", DataType.INTEGER), Literal(float("inf"), DataType.FLOAT)):
            values = self.values()
            values[0] = value
            with self.subTest(value=value), self.assertRaises(SemanticError):
                self.bind_node(Insert(table="student", values=values), QueryKind.INSERT)

    def test_update_binds_expression_and_filter(self) -> None:
        expr = BinaryOp(ArithOp.ADD, ColumnRef(None, "gpa"), Literal(1, DataType.INTEGER))
        condition = Comparison(CompOp.EQ, ColumnRef(None, "id"), Literal(1, DataType.INTEGER))
        node = Update(table="student", column="gpa", expression=expr, children=[
            Filter(condition=condition, children=[TableScan(table="student")]),
        ])
        self.bind_node(node, QueryKind.UPDATE)
        self.assertEqual(expr.left, ColumnRef("student", "gpa"))
        self.assertEqual(condition.left, ColumnRef("student", "id"))
        self.assertEqual(node.output_columns, [])

    def test_update_unknown_column_and_invalid_assignment_are_rejected(self) -> None:
        for column, expression in (
            ("missing", Literal(1, DataType.INTEGER)),
            ("id", ColumnRef(None, "gpa")), ("gpa", Literal("A", DataType.VARCHAR)),
            ("gpa", ColumnRef(None, "missing")),
            ("id", Literal(None, DataType.INTEGER)),
            ("name", Literal("abcdefghijk", DataType.VARCHAR)),
        ):
            node = Update(table="student", column=column, expression=expression, children=[TableScan(table="student")])
            with self.subTest(column=column, expression=expression), self.assertRaises(SemanticError):
                self.bind_node(node, QueryKind.UPDATE)

    def test_update_and_delete_source_must_preserve_target_rid(self) -> None:
        for source in (
            TableScan(table="course"),
            Project(columns=[ColumnRef(None, "id")], children=[TableScan(table="student")]),
            Join(kind=JoinKind.CROSS, children=[TableScan(table="student"), TableScan(table="course")]),
        ):
            for kind in (QueryKind.UPDATE, QueryKind.DELETE):
                kwargs = {"column": "id", "expression": Literal(1, DataType.INTEGER)} if kind is QueryKind.UPDATE else {}
                node_type = Update if kind is QueryKind.UPDATE else Delete
                with self.subTest(source=source, kind=kind), self.assertRaises(SemanticError):
                    self.bind_node(node_type(table="student", children=[copy.deepcopy(source)], **kwargs), kind)

    def test_delete_binds_filter_and_preserves_rid_source(self) -> None:
        condition = Comparison(CompOp.LT, ColumnRef(None, "gpa"), Literal(2, DataType.INTEGER))
        node = Delete(table="student", children=[Filter(condition=condition, children=[TableScan(table="student")])])
        self.bind_node(node, QueryKind.DELETE)
        self.assertEqual(condition.left, ColumnRef("student", "gpa"))
        self.assertEqual(node.output_columns, [])

    def test_write_command_cannot_be_child_of_row_operator(self) -> None:
        with self.assertRaises(SemanticError):
            self.bind_node(Project(columns=[], children=[Insert(table="student", values=self.values())]))


class TestIndexBinding(BinderCase):
    def test_create_index_validates_existing_table_and_column(self) -> None:
        index = IndexInfo("student_id", "student", "id")
        self.bind_node(CreateIndex(index=index), QueryKind.CREATE_INDEX)
        for table, column in (("missing", "id"), ("student", "missing")):
            with self.subTest(table=table, column=column), self.assertRaises(SemanticError):
                self.bind_node(CreateIndex(index=IndexInfo("new", table, column)), QueryKind.CREATE_INDEX)

    def test_duplicate_index_name_or_indexed_column_is_rejected(self) -> None:
        self.schemas["student"].indexes = [IndexInfo("student_id", "student", "id")]
        for index in (IndexInfo("student_id", "student", "name"), IndexInfo("new", "student", "id")):
            with self.subTest(index=index), self.assertRaises(SemanticError):
                self.bind_node(CreateIndex(index=index), QueryKind.CREATE_INDEX)

    def test_invalid_index_kind_and_empty_name_are_rejected(self) -> None:
        for index in (IndexInfo("", "student", "id"), IndexInfo("new", "student", "id", kind="BTREE")):
            with self.subTest(index=index), self.assertRaises(SemanticError):
                self.bind_node(CreateIndex(index=index), QueryKind.CREATE_INDEX)

    def test_index_scan_populates_full_table_output(self) -> None:
        index = IndexInfo("student_id", "student", "id")
        self.schemas["student"].indexes = [index]
        node = IndexScan(table="student", index=index, op=CompOp.EQ, key=1)
        self.bind_node(node)
        self.assertEqual(node.output_columns, [ColumnRef("student", name) for name in ("id", "name", "gpa")])

    def test_index_scan_metadata_must_match_catalog(self) -> None:
        index = IndexInfo("student_id", "student", "id")
        with self.assertRaises(SemanticError):
            self.bind_node(IndexScan(table="student", index=index, op=CompOp.EQ, key=1))
        self.schemas["student"].indexes = [index]
        with self.assertRaises(SemanticError):
            self.bind_node(IndexScan(table="course", index=index, op=CompOp.EQ, key=1))

    def test_hash_range_and_incompatible_search_key_are_rejected(self) -> None:
        index = IndexInfo("student_id", "student", "id", kind=IndexKind.HASH)
        self.schemas["student"].indexes = [index]
        for op, kwargs in ((CompOp.GT, {"low": 1}), (CompOp.EQ, {"key": "x"})):
            with self.subTest(op=op), self.assertRaises(SemanticError):
                self.bind_node(IndexScan(table="student", index=index, op=op, **kwargs))


class TestBindQueryHelper(BinderCase):
    def test_helper_uses_supplied_catalog(self) -> None:
        query = self.parse("SELECT name FROM student")
        self.assertIs(bind_query(query, self.sm), query)
        self.assertEqual(query.plan.columns, [ColumnRef("student", "name")])

    def test_helper_without_catalog_cannot_silently_validate_data(self) -> None:
        with self.assertRaises(SemanticError):
            bind_query(self.parse("SELECT name FROM student"))


if __name__ == "__main__":
    unittest.main()
