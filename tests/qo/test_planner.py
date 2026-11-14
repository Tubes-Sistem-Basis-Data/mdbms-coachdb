"""Unit test untuk Planner (QO-14: Memilih Query Plan Terbaik)."""

from __future__ import annotations

import copy
import unittest

from common.catalog import ColumnSchema, DataType, IndexInfo, IndexKind, TableSchema, TableStats
from common.errors import TableNotFoundError
from common.expression import And, ColumnRef, Comparison, CompOp, Literal
from common.plan import (
    CreateIndex,
    Delete,
    Filter,
    IndexScan,
    Insert,
    Join,
    JoinKind,
    ParsedQuery,
    PlanNode,
    Project,
    QueryKind,
    TableScan,
    TransactionCommand,
    Update,
)
from query_optimizer.binder import Binder
from query_optimizer.cost_model import CostModel
from query_optimizer.genetic_join_optimizer import GeneticJoinOptimizer
from query_optimizer.optimizer import QueryOptimizerFacade
from query_optimizer.planner import Planner


class _PlannerCatalog:
    """Katalog in-memory dengan indeks dan statistik untuk pengujian QO-14."""

    def __init__(self) -> None:
        self.idx_student_id = IndexInfo("idx_student_id", "student", "id", IndexKind.BPLUS_TREE)
        self.idx_student_gpa = IndexInfo("idx_student_gpa", "student", "gpa", IndexKind.BPLUS_TREE)
        self.idx_course_id = IndexInfo("idx_course_id", "course", "id", IndexKind.HASH)

        self.schemas = {
            "student": TableSchema(
                "student",
                [
                    ColumnSchema("id", DataType.INTEGER, nullable=False),
                    ColumnSchema("name", DataType.VARCHAR, length=50),
                    ColumnSchema("gpa", DataType.FLOAT),
                ],
                primary_key=["id"],
                indexes=[self.idx_student_id, self.idx_student_gpa],
            ),
            "course": TableSchema(
                "course",
                [
                    ColumnSchema("id", DataType.INTEGER, nullable=False),
                    ColumnSchema("name", DataType.VARCHAR, length=60),
                    ColumnSchema("credits", DataType.INTEGER),
                ],
                primary_key=["id"],
                indexes=[self.idx_course_id],
            ),
            "enrollment": TableSchema(
                "enrollment",
                [
                    ColumnSchema("student_id", DataType.INTEGER, nullable=False),
                    ColumnSchema("course_id", DataType.INTEGER, nullable=False),
                    ColumnSchema("grade", DataType.FLOAT),
                ],
                primary_key=["student_id", "course_id"],
            ),
            "tiny": TableSchema(
                "tiny",
                [
                    ColumnSchema("id", DataType.INTEGER, nullable=False),
                    ColumnSchema("val", DataType.INTEGER),
                ],
                primary_key=["id"],
                indexes=[IndexInfo("idx_tiny_val", "tiny", "val", IndexKind.BPLUS_TREE)],
            ),
        }
        self.stats = {
            "student": TableStats("student", 1000, 50, 62.0, 20, {"id": 1000, "name": 990, "gpa": 100}),
            "course": TableStats("course", 50, 2, 72.0, 25, {"id": 50, "name": 50, "credits": 3}),
            "enrollment": TableStats("enrollment", 5000, 100, 16.0, 50, {"student_id": 1000, "course_id": 50, "grade": 40}),
            "tiny": TableStats("tiny", 10, 1, 16.0, 20, {"id": 10, "val": 2}),
        }

    def list_tables(self) -> list[str]:
        return list(self.schemas)

    def get_schema(self, table: str) -> TableSchema:
        if table not in self.schemas:
            raise TableNotFoundError(f"Tabel tidak ditemukan: {table}", table)
        return self.schemas[table]

    def get_stats(self, table: str) -> TableStats:
        self.get_schema(table)
        return self.stats[table]


def _collect_nodes(plan: PlanNode, cls: type) -> list[PlanNode]:
    found: list[PlanNode] = []
    stack = [plan]
    while stack:
        node = stack.pop()
        if isinstance(node, cls):
            found.append(node)
        stack.extend(reversed(node.children))
    return found


class TestPlannerPickCheapest(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = _PlannerCatalog()
        self.cost_model = CostModel(self.catalog)
        self.planner = Planner(self.cost_model)

    def test_pick_cheapest_selects_lowest_cost_plan(self) -> None:
        table_scan_plan = Filter(
            condition=Comparison(CompOp.EQ, ColumnRef("student", "id"), Literal(10, DataType.INTEGER)),
            children=[TableScan(table="student")],
        )
        index_scan_plan = IndexScan(
            table="student",
            index=self.catalog.idx_student_id,
            op=CompOp.EQ,
            key=10,
        )

        best = self.planner._pick_cheapest([table_scan_plan, index_scan_plan])
        self.assertIsInstance(best, IndexScan)
        self.assertEqual(best.estimated_cost, 1.0)

    def test_pick_cheapest_keeps_first_candidate_on_equal_cost(self) -> None:
        plan_a = TableScan(table="course", alias="c1")
        plan_b = TableScan(table="course", alias="c2")

        best = self.planner._pick_cheapest([plan_a, plan_b])
        self.assertIsNot(best, plan_a)
        self.assertIsInstance(best, TableScan)
        self.assertEqual(best.alias, "c1")

    def test_pick_cheapest_skips_invalid_candidates(self) -> None:
        invalid_plan = TableScan(table="non_existent_table")
        valid_plan = TableScan(table="course")

        best = self.planner._pick_cheapest([invalid_plan, valid_plan])
        self.assertIsInstance(best, TableScan)
        self.assertEqual(best.table, "course")

    def test_pick_cheapest_empty_returns_sentinel_plan_node(self) -> None:
        best = self.planner._pick_cheapest([])
        self.assertEqual(type(best), PlanNode)


class TestPlannerOptimization(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = _PlannerCatalog()
        self.cost_model = CostModel(self.catalog)
        self.planner = Planner(self.cost_model)
        self.binder = Binder(self.catalog)

    def test_single_table_select_uses_index_scan_when_cheaper(self) -> None:
        plan = Project(
            columns=[ColumnRef("student", "name")],
            children=[
                Filter(
                    condition=Comparison(CompOp.EQ, ColumnRef("student", "id"), Literal(42, DataType.INTEGER)),
                    children=[TableScan(table="student")],
                )
            ],
        )
        query = self.binder.bind(ParsedQuery(sql="", kind=QueryKind.SELECT, plan=plan))
        before_cost = self.cost_model.get_cost(query.plan)

        optimized = self.planner.optimize(copy.deepcopy(query))
        self.binder.bind(optimized)

        self.assertLess(optimized.estimated_cost, before_cost)
        self.assertEqual(len(_collect_nodes(optimized.plan, IndexScan)), 1)
        self.assertEqual(len(_collect_nodes(optimized.plan, TableScan)), 0)

    def test_keeps_table_scan_when_index_scan_not_cheaper(self) -> None:
        plan = Filter(
            condition=Comparison(CompOp.EQ, ColumnRef("tiny", "val"), Literal(1, DataType.INTEGER)),
            children=[TableScan(table="tiny")],
        )
        query = self.binder.bind(ParsedQuery(sql="", kind=QueryKind.SELECT, plan=plan))
        optimized = self.planner.optimize(copy.deepcopy(query))

        self.assertEqual(len(_collect_nodes(optimized.plan, TableScan)), 1)
        self.assertEqual(len(_collect_nodes(optimized.plan, IndexScan)), 0)

    def test_combines_join_rule_index_scan_and_join_reordering(self) -> None:
        # Cross join (enrollment x student) dengan filter student.id = 7 AND student.id = enrollment.student_id
        # Tanpa optimasi: enrollment (100 blok) di kiri, student (50 blok) di kanan -> sangat mahal.
        # Dengan QO-11 + QO-12 + QO-13 + QO-14:
        # - Cross + Filter menjadi Theta Join
        # - student.id = 7 memakai IndexScan (1 blok)
        # - Urutan join ditukar supaya IndexScan(student) berada di sisi kiri BNLJ
        cross = Join(
            kind=JoinKind.CROSS,
            children=[TableScan(table="enrollment"), TableScan(table="student")],
        )
        cond = And(
            [
                Comparison(CompOp.EQ, ColumnRef("student", "id"), Literal(7, DataType.INTEGER)),
                Comparison(CompOp.EQ, ColumnRef("student", "id"), ColumnRef("enrollment", "student_id")),
            ]
        )
        plan = Project(
            columns=[ColumnRef("student", "name"), ColumnRef("enrollment", "grade")],
            children=[Filter(condition=cond, children=[cross])],
        )
        query = self.binder.bind(ParsedQuery(sql="", kind=QueryKind.SELECT, plan=plan))
        cost_before = self.cost_model.get_cost(query.plan)

        optimized = self.planner.optimize(copy.deepcopy(query))
        self.binder.bind(optimized)

        self.assertLess(optimized.estimated_cost, cost_before)
        joins = _collect_nodes(optimized.plan, Join)
        self.assertEqual(len(joins), 1)
        self.assertEqual(joins[0].kind, JoinKind.THETA)
        self.assertEqual(len(_collect_nodes(optimized.plan, IndexScan)), 1)

        # Pastikan sisi kiri join memiliki IndexScan(student) yang ukurannya hanya 1 blok
        left_index_scans = _collect_nodes(joins[0].children[0], IndexScan)
        self.assertEqual(len(left_index_scans), 1)
        self.assertEqual(left_index_scans[0].table, "student")

    def test_three_table_comma_join_through_facade_optimizes_completely(self) -> None:
        qo = QueryOptimizerFacade(self.catalog)
        sql = (
            "SELECT student.name, course.name, enrollment.grade "
            "FROM enrollment, student, course "
            "WHERE student.id = enrollment.student_id "
            "AND course.id = enrollment.course_id "
            "AND student.id = 5;"
        )
        parsed = qo.parse_query(sql)
        before_cost = parsed.estimated_cost

        optimized = qo.optimize_query(copy.deepcopy(parsed))

        self.assertLess(optimized.estimated_cost, before_cost)
        joins = _collect_nodes(optimized.plan, Join)
        self.assertEqual(len(joins), 2)
        self.assertTrue(all(j.kind == JoinKind.THETA for j in joins))
        index_scans = _collect_nodes(optimized.plan, IndexScan)
        self.assertGreaterEqual(len(index_scans), 1)
        self.assertEqual(index_scans[0].table, "student")

    def test_update_and_delete_can_use_index_scan_without_project(self) -> None:
        qo = QueryOptimizerFacade(self.catalog)

        upd_parsed = qo.parse_query("UPDATE student SET gpa = 3.9 WHERE id = 10;")
        upd_opt = qo.optimize_query(copy.deepcopy(upd_parsed))
        self.assertIsInstance(upd_opt.plan, Update)
        self.assertEqual(len(_collect_nodes(upd_opt.plan, Project)), 0)
        self.assertEqual(len(_collect_nodes(upd_opt.plan, IndexScan)), 1)
        self.assertLess(upd_opt.estimated_cost, upd_parsed.estimated_cost)

        del_parsed = qo.parse_query("DELETE FROM student WHERE id = 10;")
        del_opt = qo.optimize_query(copy.deepcopy(del_parsed))
        self.assertIsInstance(del_opt.plan, Delete)
        self.assertEqual(len(_collect_nodes(del_opt.plan, Project)), 0)
        self.assertEqual(len(_collect_nodes(del_opt.plan, IndexScan)), 1)
        self.assertLess(del_opt.estimated_cost, del_parsed.estimated_cost)

    def test_non_row_and_transaction_queries_handled_safely(self) -> None:
        tx_query = ParsedQuery(sql="BEGIN;", kind=QueryKind.TRANSACTION, command=TransactionCommand.BEGIN, plan=None)
        self.assertIs(self.planner.optimize(tx_query), tx_query)

        ins_plan = Insert(table="course", values=[Literal(10, DataType.INTEGER), Literal("AI", DataType.VARCHAR), Literal(3, DataType.INTEGER)])
        ins_query = ParsedQuery(sql="", kind=QueryKind.INSERT, plan=ins_plan)
        opt_ins = self.planner.optimize(ins_query)
        self.assertIsInstance(opt_ins.plan, Insert)
        self.assertEqual(opt_ins.estimated_cost, 0.0)

        idx_plan = CreateIndex(index=IndexInfo("idx_c_credits", "course", "credits", IndexKind.BPLUS_TREE))
        idx_query = ParsedQuery(sql="", kind=QueryKind.CREATE_INDEX, plan=idx_plan)
        opt_idx = self.planner.optimize(idx_query)
        self.assertIsInstance(opt_idx.plan, CreateIndex)
        self.assertEqual(opt_idx.estimated_cost, 0.0)


class TestPlannerJoinSearchStrategies(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = _PlannerCatalog()
        self.cost_model = CostModel(self.catalog)

    def test_invalid_join_search_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            Planner(self.cost_model, join_search="unknown")

    def test_ga_join_search_delegates_to_genetic_optimizer(self) -> None:
        called = False

        class _SpyGA(GeneticJoinOptimizer):
            def search(self, plan: PlanNode, cost_model: CostModel) -> PlanNode:
                nonlocal called
                called = True
                return super().search(plan, cost_model)

        planner = Planner(self.cost_model, join_search="ga", genetic=_SpyGA())
        plan = Join(
            kind=JoinKind.THETA,
            condition=Comparison(CompOp.EQ, ColumnRef("student", "id"), ColumnRef("enrollment", "student_id")),
            children=[TableScan(table="enrollment"), TableScan(table="student")],
        )
        query = Binder(self.catalog).bind(ParsedQuery(sql="", kind=QueryKind.SELECT, plan=plan))
        planner.optimize(query)
        self.assertTrue(called)


if __name__ == "__main__":
    unittest.main()
