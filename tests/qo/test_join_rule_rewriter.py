"""QO-11: pengujian JoinRuleRewriter — mengubah Cartesian Product menjadi Join.

Skenario yang diuji:
  1. Cross + filter join-condition → Theta Join (predikat jadi ON)
  2. Cross + filter campuran → Theta Join + Filter sisa
  3. Cross + filter hanya satu sisi → tetap Cross (predikat bukan join)
  4. Cross tanpa Filter → tetap Cross
  5. Theta Join eksplisit → tidak diubah
  6. Natural Join → tidak diubah
  7. Transformasi rekursif (nested cross)
  8. Kondisi AND datar maupun bersarang
  9. Predikat literal-to-literal → bukan join predikat, tetap sisa filter
 10. Kolom tanpa kualifikasi tabel (table=None) → tidak dianggap join predikat
"""

from __future__ import annotations

import unittest

from common.expression import And, Comparison, CompOp, ColumnRef, Literal
from common.catalog import DataType
from common.plan import Filter, Join, JoinKind, Project, TableScan, PlanNode
from query_optimizer.join_rule_rewriter import JoinRuleRewriter


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _scan(table: str) -> TableScan:
    return TableScan(table=table)


def _cross(left: PlanNode, right: PlanNode) -> Join:
    return Join(kind=JoinKind.CROSS, children=[left, right])


def _theta(cond, left: PlanNode, right: PlanNode) -> Join:
    return Join(kind=JoinKind.THETA, condition=cond, children=[left, right])


def _col(table: str | None, column: str) -> ColumnRef:
    return ColumnRef(table=table, column=column)


def _lit(val: int) -> Literal:
    return Literal(value=val, type=DataType.INTEGER)


def _eq(left, right) -> Comparison:
    return Comparison(op=CompOp.EQ, left=left, right=right)


def _and(*conds) -> And:
    return And(conditions=list(conds))


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestJoinRuleRewriter(unittest.TestCase):

    def setUp(self) -> None:
        self.rw = JoinRuleRewriter()

    def apply(self, plan: PlanNode) -> PlanNode:
        return self.rw.apply(plan)

    # ------------------------------------------------------------------
    # 1. Transformasi dasar: Cross + join-predicate → Theta Join
    # ------------------------------------------------------------------
    def test_cross_with_join_condition_becomes_theta(self) -> None:
        """Filter(a.id = b.a_id, Cross(A, B)) → Theta(a.id = b.a_id, A, B)"""
        cond = _eq(_col("a", "id"), _col("b", "a_id"))
        plan = Filter(condition=cond, children=[_cross(_scan("a"), _scan("b"))])
        result = self.apply(plan)
        self.assertIsInstance(result, Join)
        self.assertEqual(result.kind, JoinKind.THETA)
        self.assertEqual(result.condition, cond)
        self.assertIsInstance(result.children[0], TableScan)
        self.assertIsInstance(result.children[1], TableScan)
        self.assertEqual(result.children[0].table, "a")
        self.assertEqual(result.children[1].table, "b")

    # ------------------------------------------------------------------
    # 2. Campuran join-predicate dan sisa predicate
    # ------------------------------------------------------------------
    def test_mixed_conditions_split_correctly(self) -> None:
        """Filter(AND(a.id=b.id, a.val>0), Cross) → Filter(a.val>0, Theta(a.id=b.id))"""
        join_cond = _eq(_col("a", "id"), _col("b", "id"))
        rest_cond = Comparison(op=CompOp.GT, left=_col("a", "val"), right=_lit(0))
        big_and = _and(join_cond, rest_cond)
        plan = Filter(condition=big_and, children=[_cross(_scan("a"), _scan("b"))])
        result = self.apply(plan)

        # Hasil luar harus Filter sisa
        self.assertIsInstance(result, Filter)
        self.assertEqual(result.condition, rest_cond)

        # Anak Filter harus Theta Join
        inner = result.children[0]
        self.assertIsInstance(inner, Join)
        self.assertEqual(inner.kind, JoinKind.THETA)
        self.assertEqual(inner.condition, join_cond)

    # ------------------------------------------------------------------
    # 3. Filter hanya satu sisi → tetap Cross
    # ------------------------------------------------------------------
    def test_single_side_filter_keeps_cross(self) -> None:
        """Filter(a.val=1, Cross(A, B)) → tidak berubah (predikat hanya ke A)"""
        cond = _eq(_col("a", "val"), _lit(1))
        plan = Filter(condition=cond, children=[_cross(_scan("a"), _scan("b"))])
        result = self.apply(plan)
        self.assertIsInstance(result, Filter)
        inner = result.children[0]
        self.assertIsInstance(inner, Join)
        self.assertEqual(inner.kind, JoinKind.CROSS)

    # ------------------------------------------------------------------
    # 4. Cross tanpa Filter → tidak berubah
    # ------------------------------------------------------------------
    def test_cross_without_filter_unchanged(self) -> None:
        """Cross(A, B) → tetap Cross (tidak ada predikat sama sekali)"""
        plan = _cross(_scan("a"), _scan("b"))
        result = self.apply(plan)
        self.assertIsInstance(result, Join)
        self.assertEqual(result.kind, JoinKind.CROSS)

    # ------------------------------------------------------------------
    # 5. Theta Join eksplisit → tidak disentuh
    # ------------------------------------------------------------------
    def test_explicit_theta_join_unchanged(self) -> None:
        cond = _eq(_col("a", "id"), _col("b", "id"))
        plan = _theta(cond, _scan("a"), _scan("b"))
        result = self.apply(plan)
        self.assertIsInstance(result, Join)
        self.assertEqual(result.kind, JoinKind.THETA)
        self.assertEqual(result.condition, cond)

    # ------------------------------------------------------------------
    # 6. Natural Join → tidak disentuh
    # ------------------------------------------------------------------
    def test_natural_join_unchanged(self) -> None:
        plan = Join(kind=JoinKind.NATURAL, children=[_scan("a"), _scan("b")])
        result = self.apply(plan)
        self.assertIsInstance(result, Join)
        self.assertEqual(result.kind, JoinKind.NATURAL)

    # ------------------------------------------------------------------
    # 7. Rekursif: Cross nested (3 tabel)
    # ------------------------------------------------------------------
    def test_recursive_nested_cross(self) -> None:
        """Filter(b.id=c.id, Theta(a.id=b.id, Cross(A,B), C)) setelah dua tahap rewrite.

        Pohon awal:
            Filter(a.id=b.id AND b.id=c.id,
                   Cross(Cross(A, B), C))

        Tahap bottom-up:
          1. inner_cross = Cross(A, B)  → tidak ada Filter, tetap Cross.
          2. outer = Cross(Cross(A,B), C)  → tidak ada Filter, tetap Cross.
          3. Filter level atas mempartisi AND:
             - left_tables = {a, b}  (sumber outer.children[0] = Cross(A,B))
             - right_tables = {c}
             - cond_bc (b.id=c.id) menyentuh kiri (b) DAN kanan (c) → join pred
             - cond_ab (a.id=b.id) hanya menyentuh kiri → sisa pred
          Hasil: Filter(a.id=b.id, Theta(b.id=c.id, Cross(A,B), C))
        """
        cond_ab = _eq(_col("a", "id"), _col("b", "id"))
        cond_bc = _eq(_col("b", "id"), _col("c", "id"))
        inner_cross = _cross(_scan("a"), _scan("b"))
        outer_cross = _cross(inner_cross, _scan("c"))
        plan = Filter(condition=_and(cond_ab, cond_bc), children=[outer_cross])
        result = self.apply(plan)

        # Luar: Filter sisa (cond_ab)
        self.assertIsInstance(result, Filter)
        self.assertEqual(result.condition, cond_ab)

        # Anak Filter: Theta(b.id=c.id, ...)
        theta_outer = result.children[0]
        self.assertIsInstance(theta_outer, Join)
        self.assertEqual(theta_outer.kind, JoinKind.THETA)
        self.assertEqual(theta_outer.condition, cond_bc)

        # Cucu: sisi kiri Theta adalah Cross(A, B) yang masih CROSS
        left_of_theta = theta_outer.children[0]
        self.assertIsInstance(left_of_theta, Join)
        self.assertEqual(left_of_theta.kind, JoinKind.CROSS)

    # ------------------------------------------------------------------
    # 8. AND bersarang diratakan sebelum dipartisi
    # ------------------------------------------------------------------
    def test_nested_and_is_flattened(self) -> None:
        """AND(AND(a.id=b.id, a.x>0), b.y<10) diperlakukan seperti AND datar."""
        join_cond = _eq(_col("a", "id"), _col("b", "id"))
        rest1 = Comparison(op=CompOp.GT, left=_col("a", "x"), right=_lit(0))
        rest2 = Comparison(op=CompOp.LT, left=_col("b", "y"), right=_lit(10))
        # Buat AND bersarang: AND(AND(join, rest1), rest2)
        nested = _and(_and(join_cond, rest1), rest2)
        plan = Filter(condition=nested, children=[_cross(_scan("a"), _scan("b"))])
        result = self.apply(plan)
        # Harus ada Theta Join di dalam (mungkin dibungkus Filter sisa)
        inner = result
        if isinstance(inner, Filter):
            inner = inner.children[0]
        self.assertIsInstance(inner, Join)
        self.assertEqual(inner.kind, JoinKind.THETA)

    # ------------------------------------------------------------------
    # 9. Predikat literal-to-literal bukan join predicate
    # ------------------------------------------------------------------
    def test_literal_comparison_not_treated_as_join(self) -> None:
        """Comparison(1=1) tidak menyentuh tabel manapun → tetap rest_pred."""
        join_cond = _eq(_col("a", "id"), _col("b", "id"))
        literal_cond = _eq(_lit(1), _lit(1))
        plan = Filter(
            condition=_and(join_cond, literal_cond),
            children=[_cross(_scan("a"), _scan("b"))],
        )
        result = self.apply(plan)
        # join_cond harus menjadi Theta, literal_cond harus jadi sisa Filter
        self.assertIsInstance(result, Filter)
        self.assertEqual(result.condition, literal_cond)
        inner = result.children[0]
        self.assertIsInstance(inner, Join)
        self.assertEqual(inner.kind, JoinKind.THETA)
        self.assertEqual(inner.condition, join_cond)

    # ------------------------------------------------------------------
    # 10. Kolom tanpa kualifikasi tabel (table=None) tidak dianggap join pred
    # ------------------------------------------------------------------
    def test_unqualified_column_not_treated_as_join(self) -> None:
        """ColumnRef(table=None) tidak menyentuh tabel → bukan join predicate."""
        cond = _eq(_col(None, "id"), _lit(1))
        plan = Filter(condition=cond, children=[_cross(_scan("a"), _scan("b"))])
        result = self.apply(plan)
        # Predikat tidak memenuhi syarat join → tetap Cross
        self.assertIsInstance(result, Filter)
        inner = result.children[0]
        self.assertIsInstance(inner, Join)
        self.assertEqual(inner.kind, JoinKind.CROSS)

    # ------------------------------------------------------------------
    # 11. Rewriter idempoten (apply dua kali hasilnya sama)
    # ------------------------------------------------------------------
    def test_rewrite_is_idempotent(self) -> None:
        """Menerapkan rewriter dua kali menghasilkan plan yang sama."""
        cond = _eq(_col("a", "id"), _col("b", "id"))
        plan = Filter(condition=cond, children=[_cross(_scan("a"), _scan("b"))])
        once = self.apply(plan)
        twice = self.apply(once)
        # Dua kali apply: hasilnya masih Theta (bukan Cross)
        self.assertIsInstance(twice, Join)
        self.assertEqual(twice.kind, JoinKind.THETA)

    # ------------------------------------------------------------------
    # 12. Node selain Join/Filter tidak terpengaruh
    # ------------------------------------------------------------------
    def test_non_join_nodes_passthrough(self) -> None:
        """Project dan TableScan tidak diubah."""
        project = Project(columns=[_col("a", "id")], children=[_scan("a")])
        result = self.apply(project)
        self.assertIsInstance(result, Project)
        self.assertIsInstance(result.children[0], TableScan)

    # ------------------------------------------------------------------
    # 13. Filter di atas non-Cross (Filter tunggal) → tidak berubah
    # ------------------------------------------------------------------
    def test_filter_over_tablescan_unchanged(self) -> None:
        """Filter(cond, TableScan) → tidak berubah (anak bukan Cross)."""
        cond = _eq(_col("a", "id"), _lit(5))
        plan = Filter(condition=cond, children=[_scan("a")])
        result = self.apply(plan)
        self.assertIsInstance(result, Filter)
        self.assertIsInstance(result.children[0], TableScan)

    # ------------------------------------------------------------------
    # 14. Semua predikat adalah join predicates → tidak ada Filter sisa
    # ------------------------------------------------------------------
    def test_all_join_predicates_no_remaining_filter(self) -> None:
        """AND(a.id=b.id, a.x=b.x): semua join → Theta tanpa Filter sisa."""
        cond1 = _eq(_col("a", "id"), _col("b", "id"))
        cond2 = _eq(_col("a", "x"), _col("b", "x"))
        plan = Filter(condition=_and(cond1, cond2), children=[_cross(_scan("a"), _scan("b"))])
        result = self.apply(plan)
        # Harus langsung Theta, bukan Filter(Theta)
        self.assertIsInstance(result, Join)
        self.assertEqual(result.kind, JoinKind.THETA)
        self.assertIsInstance(result.condition, And)
        self.assertIn(cond1, result.condition.conditions)
        self.assertIn(cond2, result.condition.conditions)


if __name__ == "__main__":
    unittest.main()
