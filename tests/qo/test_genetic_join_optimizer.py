"""QO-18: genetic algorithm untuk urutan join."""

from __future__ import annotations

import random
import unittest
from collections import Counter
from unittest.mock import create_autospec

from common.catalog import ColumnSchema, DataType, TableSchema, TableStats
from common.expression import And, ColumnRef, Comparison, CompOp, Expression, Literal
from common.interfaces import StorageManager
from common.plan import Filter, Join, JoinKind, ParsedQuery, PlanNode, Project, QueryKind, TableScan
from query_optimizer.cost_model import CostModel
from query_optimizer.genetic_join_optimizer import (
    GeneticJoinOptimizer, fitness, order_crossover, swap_mutation,
)
from query_optimizer.join_order_optimizer import JoinOrderOptimizer
from query_optimizer.plan_printer import PlanPrinter
from query_optimizer.planner import Planner


def _col(table: str, column: str) -> ColumnRef:
    return ColumnRef(table, column)


def _eq(left: Expression, right: Expression) -> Comparison:
    return Comparison(CompOp.EQ, left, right)


def _cross(left: PlanNode, right: PlanNode) -> Join:
    return Join(kind=JoinKind.CROSS, children=[left, right])


def _theta(cond: Expression, left: PlanNode, right: PlanNode) -> Join:
    return Join(kind=JoinKind.THETA, condition=cond, children=[left, right])


# Data kecil untuk eksekutor acuan: setiap tabel punya kolom id dan ref
_DATA = {
    "a": [{"id": i, "ref": i % 3} for i in range(6)],
    "b": [{"id": i, "ref": i % 2} for i in range(4)],
    "c": [{"id": i, "ref": i % 4} for i in range(5)],
    "d": [{"id": i, "ref": i % 2} for i in range(3)],
}


def _value(expr: Expression, row: dict) -> object:
    if isinstance(expr, ColumnRef):
        return row[(expr.table, expr.column)]
    assert isinstance(expr, Literal)
    return expr.value


def _holds(expr: Expression, row: dict) -> bool:
    if isinstance(expr, And):
        return all(_holds(cond, row) for cond in expr.conditions)
    assert isinstance(expr, Comparison) and expr.op is CompOp.EQ
    return _value(expr.left, row) == _value(expr.right, row)


def _run(node: PlanNode) -> list[dict]:
    """Eksekutor naif untuk TableScan, Filter, dan Join."""
    if isinstance(node, TableScan):
        return [{(node.table, k): v for k, v in row.items()} for row in _DATA[node.table]]
    if isinstance(node, Filter):
        return [row for row in _run(node.children[0]) if _holds(node.condition, row)]
    assert isinstance(node, Join)
    left, right = (_run(child) for child in node.children)
    rows = [{**l, **r} for l in left for r in right]
    if node.condition is not None:
        rows = [row for row in rows if _holds(node.condition, row)]
    return rows


def _result(node: PlanNode) -> Counter:
    return Counter(frozenset(row.items()) for row in _run(node))


def _leaves(node: PlanNode) -> list[str]:
    if isinstance(node, TableScan):
        return [node.table]
    return [name for child in node.children for name in _leaves(child)]


def _condition_tables(expr: Expression) -> set[str]:
    if isinstance(expr, ColumnRef):
        return {expr.table}
    if isinstance(expr, And):
        return set().union(*(_condition_tables(c) for c in expr.conditions))
    if isinstance(expr, Comparison):
        return _condition_tables(expr.left) | _condition_tables(expr.right)
    return set()


def _joins(node: PlanNode) -> list[Join]:
    found = [node] if isinstance(node, Join) else []
    for child in node.children:
        found.extend(_joins(child))
    return found


class GeneticCase(unittest.TestCase):
    """Empat tabel dengan ukuran sangat berbeda supaya urutan join berpengaruh ke biaya."""

    def setUp(self) -> None:
        self.stats = {
            "a": TableStats("a", 10000, 1000, 100.0, 10, {"id": 10000, "ref": 100}),
            "b": TableStats("b", 2000, 200, 100.0, 10, {"id": 2000, "ref": 50}),
            "c": TableStats("c", 500, 50, 100.0, 10, {"id": 500, "ref": 20}),
            "d": TableStats("d", 20, 2, 100.0, 10, {"id": 20, "ref": 5}),
        }
        self.schemas = {
            name: TableSchema(name, [ColumnSchema("id", DataType.INTEGER), ColumnSchema("ref", DataType.INTEGER)])
            for name in self.stats
        }
        self.sm = create_autospec(StorageManager, instance=True, spec_set=True)
        self.sm.get_stats.side_effect = self.stats.__getitem__
        self.sm.get_schema.side_effect = self.schemas.__getitem__
        self.model = CostModel(self.sm, buffer_frames=12, block_size=1000)

    def chain(self) -> PlanNode:
        """a ⋈ b ⋈ c ⋈ d dengan urutan awal yang mahal (tabel besar di luar)."""
        ab = _theta(_eq(_col("a", "ref"), _col("b", "id")), TableScan(table="a"), TableScan(table="b"))
        abc = _theta(_eq(_col("b", "ref"), _col("c", "id")), ab, TableScan(table="c"))
        return _theta(And([_eq(_col("c", "ref"), _col("d", "id")), _eq(_col("a", "id"), Literal(1, DataType.INTEGER))]),
                      abc, TableScan(table="d"))

    def ga(self, **kwargs) -> GeneticJoinOptimizer:
        params = {"population_size": 20, "generations": 30, "mutation_rate": 0.2, "seed": 7}
        params.update(kwargs)
        return GeneticJoinOptimizer(**params)


class TestOperator(unittest.TestCase):
    def test_crossover_selalu_permutasi(self) -> None:
        rng = random.Random(1)
        for n in range(1, 9):
            for _ in range(50):
                p1, p2 = rng.sample(range(n), n), rng.sample(range(n), n)
                self.assertEqual(sorted(order_crossover(p1, p2, rng)), list(range(n)))

    def test_crossover_mempertahankan_potongan_parent1(self) -> None:
        rng = random.Random(3)
        p1, p2 = [0, 1, 2, 3, 4, 5], [5, 4, 3, 2, 1, 0]
        child = order_crossover(p1, p2, random.Random(3))
        start, end = sorted(rng.sample(range(6), 2))
        self.assertEqual(child[start:end + 1], p1[start:end + 1])

    def test_mutasi_selalu_permutasi(self) -> None:
        rng = random.Random(2)
        for _ in range(100):
            individual = rng.sample(range(6), 6)
            self.assertEqual(sorted(swap_mutation(individual, 1.0, rng)), list(range(6)))

    def test_mutasi_peluang_nol_tidak_mengubah(self) -> None:
        self.assertEqual(swap_mutation([2, 0, 1], 0.0, random.Random(0)), [2, 0, 1])

    def test_fitness(self) -> None:
        self.assertGreater(fitness(10), fitness(100))
        self.assertEqual(fitness(float("inf")), 0.0)

    def test_parameter_divalidasi(self) -> None:
        for kwargs in ({"population_size": 1}, {"generations": -1}, {"mutation_rate": 1.5},
                       {"tournament_size": 0}, {"population_size": 2.5}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    GeneticJoinOptimizer(**kwargs)


class TestSearch(GeneticCase):
    def test_hasil_ekuivalen_dengan_query_awal(self) -> None:
        plan = self.chain()
        hasil = self.ga().search(plan, self.model)
        self.assertEqual(sorted(_leaves(hasil)), ["a", "b", "c", "d"])
        self.assertEqual(_result(hasil), _result(plan))

    def test_biaya_tidak_lebih_mahal_dari_awal_dan_heuristik(self) -> None:
        plan = self.chain()
        awal = self.model.get_cost(plan)
        heuristik = self.model.get_cost(JoinOrderOptimizer().reorder(self.chain(), self.model))
        ga_cost = self.model.get_cost(self.ga().search(plan, self.model))
        self.assertLess(ga_cost, awal)
        self.assertLessEqual(ga_cost, heuristik)

    def test_kondisi_dipasang_di_join_yang_memuat_tabelnya(self) -> None:
        hasil = self.ga().search(self.chain(), self.model)
        for join in _joins(hasil):
            if join.condition is not None:
                self.assertLessEqual(_condition_tables(join.condition), set(_leaves(join)))

    def test_plan_input_tidak_diubah(self) -> None:
        plan = self.chain()
        sebelum = PlanPrinter().render(plan)
        self.ga().search(plan, self.model)
        self.assertEqual(PlanPrinter().render(plan), sebelum)

    def test_seed_sama_hasil_sama(self) -> None:
        printer = PlanPrinter()
        satu = printer.render(self.ga(seed=11).search(self.chain(), self.model))
        dua = printer.render(self.ga(seed=11).search(self.chain(), self.model))
        self.assertEqual(satu, dua)

    def test_history_tidak_naik(self) -> None:
        ga = self.ga(generations=10)
        ga.search(self.chain(), self.model)
        self.assertEqual(len(ga.history), 1)
        self.assertEqual(len(ga.history[0]), 11)
        for before, after in zip(ga.history[0], ga.history[0][1:]):
            self.assertLessEqual(after, before)

    def test_tanpa_generasi_tetap_valid(self) -> None:
        plan = self.chain()
        hasil = self.ga(generations=0, population_size=2).search(plan, self.model)
        self.assertEqual(_result(hasil), _result(plan))

    def test_cross_join_tetap_valid(self) -> None:
        plan = _cross(_cross(TableScan(table="a"), TableScan(table="d")), TableScan(table="c"))
        hasil = self.ga().search(plan, self.model)
        self.assertEqual(_result(hasil), _result(plan))
        self.assertLessEqual(self.model.get_cost(hasil), self.model.get_cost(plan))

    def test_select_bintang_tanpa_kolom_tidak_diubah(self) -> None:
        plan = Project(columns=[], children=[self.chain()])
        hasil = self.ga().search(plan, self.model)
        self.assertEqual(_leaves(hasil), ["a", "b", "c", "d"])

    def test_select_dengan_kolom_boleh_diubah(self) -> None:
        plan = Project(columns=[_col("a", "id")], children=[self.chain()])
        hasil = self.ga().search(plan, self.model)
        self.assertLess(self.model.get_cost(hasil), self.model.get_cost(plan))

    def test_join_natural_tidak_disentuh(self) -> None:
        plan = Join(kind=JoinKind.NATURAL, children=[TableScan(table="a"), TableScan(table="d")])
        hasil = self.ga().search(plan, self.model)
        self.assertEqual(_leaves(hasil), ["a", "d"])


class TestPlanner(GeneticCase):
    def test_mode_ga(self) -> None:
        planner = Planner(self.model, join_search="ga", genetic=self.ga())
        query = planner.optimize(ParsedQuery("x", QueryKind.SELECT, plan=self.chain()))
        self.assertLess(self.model.get_cost(query.plan), self.model.get_cost(self.chain()))
        self.assertEqual(_result(query.plan), _result(self.chain()))

    def test_mode_tidak_dikenal(self) -> None:
        with self.assertRaises(ValueError):
            Planner(self.model, join_search="dp")


if __name__ == "__main__":
    unittest.main()
