"""Uji QueryOptimizerFacade.explain (QO-15)."""

import unittest

from common.plan import ParsedQuery, PlanNode, Project, QueryKind, TableScan, TransactionCommand
from query_optimizer.optimizer import QueryOptimizerFacade


class _MutatingQO(QueryOptimizerFacade):
    """Optimizer yang mengubah plan langsung di tempat, untuk memastikan explain memakai salinan."""

    def optimize_query(self, query: ParsedQuery) -> ParsedQuery:
        if query.plan is not None:
            query.plan.children = [TableScan(table="hasil_optimasi")]
        return query


class _CountingCost:
    """CostModel palsu: biaya = jumlah node di plan."""

    def get_cost(self, plan: PlanNode) -> float:
        return 1.0 + sum(self.get_cost(c) for c in plan.children)


def _select() -> ParsedQuery:
    plan = Project(columns=[], children=[TableScan(table="student")])
    return ParsedQuery("EXPLAIN SELECT * FROM student;", QueryKind.EXPLAIN, plan=plan)


class TestExplain(unittest.TestCase):
    def test_format_sebelum_sesudah(self) -> None:
        qo = QueryOptimizerFacade(None)
        qo._cost_model = _CountingCost()
        self.assertEqual(qo.explain(_select()), "\n".join([
            "SEBELUM (biaya 2 blok)",
            "Project [*]",
            "  TableScan [student]",
            "",
            "SESUDAH (biaya 2 blok)",
            "Project [*]",
            "  TableScan [student]",
        ]))

    def test_plan_asli_tidak_berubah(self) -> None:
        qo = _MutatingQO(None)
        qo._cost_model = _CountingCost()
        query = _select()
        hasil = qo.explain(query)
        self.assertEqual(query.plan.children, [TableScan(table="student")])
        sebelum, sesudah = hasil.split("SESUDAH")
        self.assertIn("TableScan [student]", sebelum)
        self.assertIn("TableScan [hasil_optimasi]", sesudah)

    def test_plan_none_tidak_error(self) -> None:
        qo = QueryOptimizerFacade(None)
        qo._cost_model = _CountingCost()
        query = ParsedQuery("BEGIN;", QueryKind.TRANSACTION, command=TransactionCommand.BEGIN)
        hasil = qo.explain(query)
        self.assertIn("SEBELUM (biaya 0 blok)", hasil)
        self.assertEqual(hasil.count("(tidak ada plan)"), 2)


if __name__ == "__main__":
    unittest.main()
