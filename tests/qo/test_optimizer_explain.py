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

    def test_explain_menampilkan_perbedaan_optimasi_m3_index_dan_join(self) -> None:
        from tests.qo.test_planner import _PlannerCatalog

        qo = QueryOptimizerFacade(_PlannerCatalog())
        sql = (
            "EXPLAIN SELECT student.name, enrollment.grade "
            "FROM enrollment, student "
            "WHERE student.id = 7 AND student.id = enrollment.student_id;"
        )
        parsed = qo.parse_query(sql)
        teks = qo.explain(parsed)
        sebelum, sesudah = teks.split("SESUDAH")

        self.assertIn("Join BNLJ CROSS", sebelum)
        self.assertIn("TableScan [student]", sebelum)
        self.assertIn("Join BNLJ THETA", sesudah)
        self.assertIn("IndexScan [student.idx_student_id: id = 7]", sesudah)


if __name__ == "__main__":
    unittest.main()
