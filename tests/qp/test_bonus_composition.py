"""Uji komposisi QP-16: subquery di FROM dengan LIMIT, JOIN, SORT, dan scalar subquery."""

from __future__ import annotations

import copy
import unittest
from collections import Counter
from unittest.mock import Mock, patch

from common.catalog import ColumnSchema, DataType, TableSchema
from common.expression import ArithOp, BinaryOp, ColumnRef, Comparison, CompOp, Literal, Subquery
from common.interfaces import ConcurrencyControlManager, StorageManager
from common.plan import (
    Filter, Join, JoinKind, Limit, ParsedQuery, Project, QueryKind, Sort, SubqueryScan, TableScan,
)
from common.storage import RID, Row
from common.transaction import AccessResponse, AccessStatus
from query_processor.context import ExecContext
from query_processor.errors import ExecutionError, PlanError
from query_processor.translator import PlanTranslator
from tests.qp.test_select_bonus_integration import runtime


def ref(table: str, name: str = "id") -> ColumnRef:
    return ColumnRef(table, name)


def select(source, *columns, aliases=None) -> Project:
    return Project(columns=list(columns), aliases=list(aliases or []), children=[source])


def div_by_zero_filter(source) -> Filter:
    """Kondisi yang gagal jika benar-benar dievaluasi: 1 / 0 > 0."""
    division = BinaryOp(ArithOp.DIV, Literal(1, DataType.INTEGER), Literal(0, DataType.INTEGER))
    return Filter(condition=Comparison(CompOp.GT, division, Literal(0, DataType.INTEGER)),
                  children=[source])


def first_two_nim() -> SubqueryScan:
    """(SELECT id AS nim FROM student LIMIT 2) AS q"""
    inner = Limit(count=2, children=[select(TableScan(table="student"), ref("student"), aliases=["nim"])])
    return SubqueryScan(alias="q", children=[inner])


class TestBonusComposition(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = {
            "student": TableSchema("student", [
                ColumnSchema("id", DataType.INTEGER), ColumnSchema("name", DataType.VARCHAR, 50),
                ColumnSchema("gpa", DataType.FLOAT),
            ]),
            "course": TableSchema("course", [
                ColumnSchema("id", DataType.INTEGER), ColumnSchema("title", DataType.VARCHAR, 50),
            ]),
            "threshold": TableSchema("threshold", [ColumnSchema("gpa", DataType.FLOAT)]),
        }
        self.rows = {
            "student": [Row(RID("student", 0, i), values) for i, values in enumerate(
                ([1, "Ada", 3.0], [2, "Budi", 4.0], [3, "Cici", 2.5])
            )],
            "course": [Row(RID("course", 0, 0), [1, "DB"]), Row(RID("course", 0, 1), [3, "AI"])],
            "threshold": [Row(RID("threshold", 0, 0), [3.2])],
        }
        self.sm = Mock(spec=StorageManager)
        self.sm.get_schema.side_effect = lambda table: copy.deepcopy(self.catalog[table])
        self.sm.scan.side_effect = lambda table: iter(copy.deepcopy(self.rows[table]))
        self.ccm = Mock(spec=ConcurrencyControlManager)
        self.ccm.validate_object.return_value = AccessResponse(AccessStatus.ALLOWED)
        self.ctx = ExecContext(7, self.sm, self.ccm)
        self.translator = PlanTranslator()

    def build(self, plan, kind=QueryKind.SELECT):
        return self.translator.build(ParsedQuery("manual plan", kind, plan), self.ctx)

    def run_plan(self, plan):
        return self.build(plan).execute(self.ctx)

    def scanned(self) -> list[str]:
        return [call.args[0] for call in self.sm.scan.call_args_list]

    # --- LIMIT 0 (regresi feedback) ---------------------------------------

    def test_limit_zero_root_does_not_open_child_or_evaluate_division(self) -> None:
        plan = Limit(count=0, children=[select(div_by_zero_filter(TableScan(table="student")), ref("student"))])

        result = self.run_plan(plan)

        self.assertTrue(result.ok)
        self.assertEqual((result.columns, result.rows), (["id"], []))
        self.assertEqual(self.scanned(), [])

    def test_division_by_zero_still_fails_without_limit_zero(self) -> None:
        plan = Limit(count=1, children=[select(div_by_zero_filter(TableScan(table="student")), ref("student"))])
        with self.assertRaisesRegex(ExecutionError, "nol"):
            self.run_plan(plan)

    def test_limit_zero_inside_derived_table_does_not_read_inner_source(self) -> None:
        inner = Limit(count=0, children=[
            select(div_by_zero_filter(TableScan(table="student")), ref("student"), aliases=["nim"]),
        ])
        plan = select(SubqueryScan(alias="q", children=[inner]), ref("q", "nim"))

        result = self.run_plan(plan)

        self.assertEqual((result.columns, result.rows), (["nim"], []))
        self.assertEqual(self.scanned(), [])

    def test_scalar_subquery_with_limit_zero_is_null_without_reading_source(self) -> None:
        scalar = Subquery(Limit(count=0, children=[
            select(div_by_zero_filter(TableScan(table="threshold")), ref("threshold", "gpa")),
        ]))
        plan = select(Filter(condition=Comparison(CompOp.GT, ref("student", "gpa"), scalar),
                             children=[TableScan(table="student")]), ref("student", "name"))

        self.assertEqual(self.run_plan(plan).rows, [])
        self.assertNotIn("threshold", self.scanned())

    # --- Rantai LIMIT -----------------------------------------------------

    def test_nested_limit_chain_takes_the_smallest_bound(self) -> None:
        body = select(TableScan(table="student"), ref("student"))
        for outer, inner, expected in ((3, 2, [[1], [2]]), (1, 5, [[1]]), (0, 2, [])):
            with self.subTest(outer=outer, inner=inner):
                plan = Limit(count=outer, children=[Limit(count=inner, children=[copy.deepcopy(body)])])
                result = self.run_plan(plan)
                self.assertEqual((result.columns, result.rows), (["id"], expected))

    def test_invalid_select_limit_chains_are_rejected_before_reading_data(self) -> None:
        scan = TableScan(table="student")
        body = select(scan, ref("student"))
        invalid = {
            "Limit di atas Scan": Limit(count=1, children=[scan]),
            "Limit di atas Filter": Limit(count=1, children=[div_by_zero_filter(scan)]),
            "Limit di atas Sort": Limit(count=1, children=[Sort(column=ref("student"), children=[scan])]),
            "rantai berakhir di Scan": Limit(count=1, children=[Limit(count=1, children=[scan])]),
            "count bool": Limit(count=True, children=[body]),
            "count negatif": Limit(count=-1, children=[body]),
            "count float": Limit(count=1.0, children=[body]),
            "Limit tanpa anak": Limit(count=1, children=[]),
        }
        for label, plan in invalid.items():
            with self.subTest(label), self.assertRaises(PlanError):
                self.build(plan)
        self.sm.get_schema.assert_not_called()
        self.assertEqual(self.scanned(), [])

    def test_from_and_scalar_subqueries_require_select_shape(self) -> None:
        scan = TableScan(table="student")
        bad_inner = (scan, div_by_zero_filter(scan), Limit(count=1, children=[scan]))
        for inner in bad_inner:
            with self.subTest(inner=type(inner).__name__):
                with self.assertRaises(PlanError):
                    self.build(select(SubqueryScan(alias="q", children=[inner]), ref("q")))
                scalar = Subquery(inner)
                with self.assertRaises(PlanError):
                    self.build(select(Filter(condition=Comparison(CompOp.GT, ref("student"), scalar),
                                             children=[TableScan(table="student")]), ref("student")))
        self.sm.get_schema.assert_not_called()

    # --- Subquery di FROM -------------------------------------------------

    def test_spec_example_derived_table_with_limit_and_column_alias(self) -> None:
        result = self.run_plan(select(first_two_nim(), ref("q", "nim")))

        self.assertEqual((result.columns, result.rows), (["nim"], [[1], [2]]))

    def test_derived_rows_have_no_rid_and_inner_prefix_is_hidden(self) -> None:
        root = self.translator.build_rows(first_two_nim(), self.ctx)
        root.open(self.ctx)
        try:
            rows = []
            while (row := root.next()) is not None:
                rows.append(row)
        finally:
            root.close()
        self.assertEqual([row.values for row in rows], [(1,), (2,)])
        self.assertTrue(all(row.rid is None for row in rows))

        for hidden in (ref("student"), ref("q", "id"), ref("student", "nim")):
            with self.subTest(ref=hidden), self.assertRaises(PlanError):
                self.build(select(first_two_nim(), hidden))

    def test_duplicate_export_names_are_rejected_and_aliases_fix_them(self) -> None:
        join = Join(kind=JoinKind.THETA, condition=Comparison(CompOp.EQ, ref("a"), ref("b")),
                    children=[TableScan(table="student", alias="a"), TableScan(table="student", alias="b")])
        duplicated = select(copy.deepcopy(join), ref("a"), ref("b"))
        with self.assertRaisesRegex(PlanError, "berulang"):
            self.build(select(SubqueryScan(alias="q", children=[duplicated]), ref("q")))

        same_alias = select(copy.deepcopy(join), ref("a"), ref("b", "name"), aliases=["x", "x"])
        with self.assertRaisesRegex(PlanError, "berulang"):
            self.build(select(SubqueryScan(alias="q", children=[same_alias]), ref("q", "x")))
        self.assertEqual(self.scanned(), [])

        renamed = select(copy.deepcopy(join), ref("a"), ref("b"), aliases=["a_id", "b_id"])
        result = self.run_plan(select(SubqueryScan(alias="q", children=[renamed]),
                                      ref("q", "a_id"), ref("q", "b_id")))
        self.assertCountEqual(result.rows, [[1, 1], [2, 2], [3, 3]])

    def test_duplicate_labels_on_plain_select_are_still_allowed(self) -> None:
        plan = select(TableScan(table="student"), ref("student"), ref("student", "name"), aliases=["x", "x"])
        self.assertEqual(self.run_plan(plan).columns, ["x", "x"])

    def test_nested_derived_tables(self) -> None:
        middle = select(first_two_nim(), ref("q", "nim"), aliases=["x"])
        plan = select(SubqueryScan(alias="r", children=[middle]), ref("r", "x"))

        result = self.run_plan(plan)

        self.assertEqual((result.columns, result.rows), (["x"], [[1], [2]]))
        with self.assertRaises(PlanError):
            self.build(select(SubqueryScan(alias="r", children=[copy.deepcopy(middle)]), ref("q", "nim")))

    def test_two_derived_tables_with_different_aliases_do_not_collide(self) -> None:
        other = copy.deepcopy(first_two_nim())
        other.alias = "p"
        join = Join(kind=JoinKind.THETA, condition=Comparison(CompOp.EQ, ref("q", "nim"), ref("p", "nim")),
                    children=[first_two_nim(), other])

        self.assertCountEqual(self.run_plan(select(join, ref("q", "nim"), ref("p", "nim"))).rows,
                              [[1, 1], [2, 2]])

        clash = Join(kind=JoinKind.CROSS, children=[first_two_nim(), first_two_nim()])
        with self.assertRaises(PlanError):
            self.build(select(clash, ref("q", "nim")))

    def test_derived_table_joins_physical_table_and_is_rescanned_as_inner(self) -> None:
        for derived_on_right in (False, True):
            with self.subTest(derived_on_right=derived_on_right):
                self.sm.scan.reset_mock()
                course = TableScan(table="course", alias="c")
                children = [course, first_two_nim()] if derived_on_right else [first_two_nim(), course]
                join = Join(kind=JoinKind.THETA, condition=Comparison(CompOp.EQ, ref("q", "nim"), ref("c")),
                            children=children)

                result = self.run_plan(select(join, ref("q", "nim"), ref("c", "title")))

                self.assertEqual(result.rows, [[1, "DB"]])
                if derived_on_right:
                    # BNLJ membuka ulang inner untuk setiap blok outer (2 baris course).
                    self.assertEqual(self.scanned().count("student"), 2)

    def test_where_sort_and_limit_on_outer_query_over_derived_table(self) -> None:
        inner = select(TableScan(table="student"), ref("student", "name"), ref("student", "gpa"),
                       aliases=["nama", None])
        source = SubqueryScan(alias="t", children=[inner])
        filtered = Filter(condition=Comparison(CompOp.GE, ref("t", "gpa"), Literal(2.5, DataType.FLOAT)),
                          children=[source])
        plan = Limit(count=2, children=[
            select(Sort(column=ref("t", "gpa"), ascending=False, children=[filtered]), ref("t", "nama")),
        ])

        result = self.run_plan(plan)

        self.assertEqual((result.columns, result.rows), (["nama"], [["Budi"], ["Ada"]]))

    def test_sort_and_limit_inside_derived_table(self) -> None:
        inner = Limit(count=1, children=[
            select(Sort(column=ref("student", "gpa"), children=[TableScan(table="student")]),
                   ref("student", "name"), aliases=["terendah"]),
        ])
        plan = select(SubqueryScan(alias="m", children=[inner]), ref("m", "terendah"))

        self.assertEqual(self.run_plan(plan).rows, [["Cici"]])

    def test_scalar_subquery_over_derived_table(self) -> None:
        scalar = Subquery(select(SubqueryScan(alias="th", children=[
            select(TableScan(table="threshold"), ref("threshold", "gpa"), aliases=["batas"]),
        ]), ref("th", "batas")))
        plan = select(Filter(condition=Comparison(CompOp.GT, ref("student", "gpa"), scalar),
                             children=[TableScan(table="student")]), ref("student", "name"))

        self.assertEqual(self.run_plan(plan).rows, [["Budi"]])

    def test_three_source_join_with_derived_table_is_order_independent(self) -> None:
        def plan(order):
            sources = {
                "q": first_two_nim(),
                "c": TableScan(table="course", alias="c"),
                "s": TableScan(table="student", alias="s"),
            }
            first, second, third = (sources[name] for name in order)
            inner = Join(kind=JoinKind.CROSS, children=[first, second])
            condition = Comparison(CompOp.EQ, ref("q", "nim"), ref("s"))
            outer = Join(kind=JoinKind.THETA, condition=condition, children=[inner, third])
            return select(outer, ref("q", "nim"), ref("s", "name"), ref("c", "title"))

        baseline = Counter(map(tuple, self.run_plan(plan("qcs")).rows))
        self.assertEqual(baseline, Counter({
            (1, "Ada", "DB"): 1, (1, "Ada", "AI"): 1, (2, "Budi", "DB"): 1, (2, "Budi", "AI"): 1,
        }))
        for order in ("cqs", "sqc", "csq"):
            with self.subTest(order=order):
                self.assertEqual(Counter(map(tuple, self.run_plan(plan(order)).rows)), baseline)


class TestDerivedTableThroughRealComponents(unittest.TestCase):
    """SQL subquery di FROM melewati QO, CCM, FRM, dan SM asli."""

    def execute_ok(self, qp, session, sql):
        result = qp.execute_query(sql, session)
        self.assertTrue(result.ok, result.message)
        return result

    def test_derived_table_queries(self) -> None:
        cases = (
            ("SELECT q.nim FROM (SELECT id AS nim FROM student LIMIT 2) AS q;", ["nim"], [[1], [2]]),
            ("SELECT q.id FROM (SELECT id FROM student) q;", ["id"], [[1], [2], [3]]),
            ("SELECT t.nama FROM (SELECT name AS nama, gpa FROM student) AS t WHERE t.gpa > 3.2;",
             ["nama"], [["Budi"]]),
            ("SELECT q.nama FROM (SELECT name AS nama, gpa FROM student ORDER BY gpa DESC LIMIT 2) q;",
             ["nama"], [["Budi"], ["Ada"]]),
            ("SELECT q.id FROM (SELECT id FROM student) q ORDER BY q.id DESC LIMIT 1;", ["id"], [[3]]),
            ("SELECT r.x FROM (SELECT q.nim AS x FROM (SELECT id AS nim FROM student LIMIT 2) q) r;",
             ["x"], [[1], [2]]),
            ("SELECT q.nim, s.name FROM (SELECT id AS nim FROM student WHERE id < 3) q "
             "JOIN student s ON q.nim = s.id ORDER BY q.nim;", ["nim", "name"], [[1, "Ada"], [2, "Budi"]]),
            ("SELECT name FROM student WHERE gpa > (SELECT q.gpa FROM (SELECT gpa FROM threshold) q);",
             ["name"], [["Budi"]]),
        )
        for protocol in ("lock", "timestamp"):
            with self.subTest(protocol=protocol), runtime(protocol) as (qp, session):
                for sql, columns, rows in cases:
                    with self.subTest(sql=sql):
                        result = self.execute_ok(qp, session, sql)
                        self.assertEqual((result.columns, result.rows), (columns, rows))
                self.assertIsNone(session.tx_id)

    def test_limit_zero_derived_table_does_not_read_or_divide(self) -> None:
        for protocol in ("lock", "timestamp"):
            with self.subTest(protocol=protocol), runtime(protocol) as (qp, session):
                with patch.object(qp._sm, "scan", wraps=qp._sm.scan) as scan:
                    result = self.execute_ok(
                        qp, session, "SELECT q.id FROM (SELECT id FROM student WHERE gpa / 0 > 1 LIMIT 0) q;")
                self.assertEqual((result.columns, result.rows), (["id"], []))
                scan.assert_not_called()

    def test_invalid_derived_table_queries_are_rejected(self) -> None:
        for protocol in ("lock", "timestamp"):
            with self.subTest(protocol=protocol), runtime(protocol) as (qp, session):
                for sql in (
                    "SELECT q.id FROM (SELECT a.id, b.id FROM student a JOIN student b ON a.id = b.id) q;",
                    "SELECT student.id FROM (SELECT id FROM student) q;",
                    "SELECT q.id FROM (SELECT id FROM student);",
                ):
                    with self.subTest(sql=sql):
                        self.assertFalse(qp.execute_query(sql, session).ok)
                        self.assertIsNone(session.tx_id)
                self.assertEqual(self.execute_ok(qp, session, "SELECT q.id FROM (SELECT id FROM student) q "
                                                 "ORDER BY q.id LIMIT 1;").rows, [[1]])


if __name__ == "__main__":
    unittest.main()
