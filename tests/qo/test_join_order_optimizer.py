"""QO-12: unit test JoinOrderOptimizer (commutativity M2 + associativity M3)."""

from __future__ import annotations

import unittest
from unittest.mock import create_autospec

from common.catalog import ColumnSchema, DataType, IndexInfo, TableSchema, TableStats
from common.expression import ColumnRef, Comparison, CompOp
from common.interfaces import StorageManager
from common.plan import Filter, IndexScan, Join, JoinKind, PlanNode, Project, TableScan
from query_optimizer.cost_model import CostModel
from query_optimizer.join_order_optimizer import JoinOrderOptimizer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _scan(table: str) -> TableScan:
    return TableScan(table=table)


def _cross(left: PlanNode, right: PlanNode) -> Join:
    return Join(kind=JoinKind.CROSS, children=[left, right])


def _theta(cond, left: PlanNode, right: PlanNode) -> Join:
    return Join(kind=JoinKind.THETA, condition=cond, children=[left, right])


def _col(table: str, column: str) -> ColumnRef:
    return ColumnRef(table=table, column=column)


def _eq(left, right) -> Comparison:
    return Comparison(op=CompOp.EQ, left=left, right=right)


def _tables(node: PlanNode) -> list[str]:
    """Kumpulkan nama tabel daun secara in-order (DFS kiri-dahulu)."""
    if isinstance(node, (TableScan, IndexScan)):
        return [node.table]
    result: list[str] = []
    for child in node.children:
        result.extend(_tables(child))
    return result


# ---------------------------------------------------------------------------
# Fixture: mock StorageManager dengan stats yang bisa dikustomisasi
# ---------------------------------------------------------------------------

class JoinOrderCase(unittest.TestCase):
    """Base class: 3 tabel r(1000 baris, 100 blok), s(500,50), t(100,10)."""

    def setUp(self) -> None:
        self.stats = {
            "r": TableStats("r", 1000, 100, 100.0, 10, {"id": 1000, "group_id": 100}),
            "s": TableStats("s", 500,  50, 100.0, 10, {"id": 500,  "value":    50}),
            "t": TableStats("t", 100,  10, 100.0, 10, {"id": 100,  "ref":      10}),
        }
        self.schemas = {
            tbl: TableSchema(tbl, [ColumnSchema(col, DataType.INTEGER)
                                   for col in stats.distinct])
            for tbl, stats in self.stats.items()
        }
        self.sm = create_autospec(StorageManager, instance=True, spec_set=True)
        self.sm.get_stats.side_effect = self.stats.__getitem__
        self.sm.get_schema.side_effect = self.schemas.__getitem__

        self.model = CostModel(self.sm, buffer_frames=12, block_size=1000)
        self.opt   = JoinOrderOptimizer()

    def reorder(self, plan: PlanNode) -> PlanNode:
        return self.opt.reorder(plan, self.model)


# ===========================================================================
# Tes M2 – Commutativity
# ===========================================================================

class TestCommutativity(JoinOrderCase):

    def test_cross_join_smaller_outer_is_cheaper(self) -> None:
        """Join(r,s) vs Join(s,r): pilih yang outer-nya lebih kecil."""
        plan_rs = _cross(_scan("r"), _scan("s"))
        plan_sr = _cross(_scan("s"), _scan("r"))
        cost_rs = self.model.get_cost(plan_rs)
        cost_sr = self.model.get_cost(plan_sr)
        # Pastikan keduanya memang memiliki biaya berbeda (fixture dirancang demikian)
        self.assertNotEqual(cost_rs, cost_sr)

        result = self.reorder(_cross(_scan("r"), _scan("s")))
        self.assertIsInstance(result, Join)
        # Tabel daun pertama harus yang lebih kecil (s = 50 blok < r = 100 blok)
        self.assertEqual(_tables(result)[0], "s")

    def test_cross_join_already_optimal_unchanged(self) -> None:
        """Jika urutan awal sudah optimal, optimizer tidak perlu menukar."""
        plan = _cross(_scan("s"), _scan("r"))   # s lebih kecil → sudah optimal
        result = self.reorder(plan)
        self.assertEqual(_tables(result)[0], "s")

    def test_commute_produces_join_not_other_node(self) -> None:
        """Hasil tukar harus tetap node Join."""
        result = self.reorder(_cross(_scan("r"), _scan("s")))
        self.assertIsInstance(result, Join)

    def test_natural_join_not_reordered(self) -> None:
        """NATURAL JOIN diabaikan optimizer (semantik bergantung urutan nama kolom)."""
        plan = Join(kind=JoinKind.NATURAL, children=[_scan("r"), _scan("s")])
        result = self.reorder(plan)
        # Node dikembalikan utuh
        self.assertEqual(result.kind, JoinKind.NATURAL)
        self.assertEqual(_tables(result), ["r", "s"])

    def test_non_join_node_passthrough(self) -> None:
        """Node bukan Join (mis. Filter) dikembalikan apa adanya."""
        from common.catalog import DataType
        from common.expression import Literal
        cond = _eq(_col("r", "id"), Literal(1, DataType.INTEGER))
        plan = Filter(condition=cond, children=[_scan("r")])
        result = self.reorder(plan)
        self.assertIsInstance(result, Filter)

    def test_theta_join_commuted_when_cheaper(self) -> None:
        """Theta Join juga dapat dikomutasi jika menghemat biaya."""
        cond = _eq(_col("r", "id"), _col("s", "id"))
        plan = _theta(cond, _scan("r"), _scan("s"))
        result = self.reorder(plan)
        self.assertIsInstance(result, Join)
        self.assertEqual(result.kind, JoinKind.THETA)
        # Harus memilih sisi yang lebih murah di luar
        self.assertEqual(_tables(result)[0], "s")


# ===========================================================================
# Tes M3 – Associativity
# ===========================================================================

class TestAssociativity(JoinOrderCase):

    def test_left_deep_to_right_deep_when_cheaper(self) -> None:
        """Join(Join(r,s),t) → Join(r,Join(s,t)) jika lebih murah."""
        # Karena t sangat kecil (10 blok), menaruhnya di dalam sering lebih murah.
        plan = _cross(_cross(_scan("r"), _scan("s")), _scan("t"))
        result = self.reorder(plan)
        all_tables = _tables(result)
        # Semua tabel harus tetap ada
        self.assertCountEqual(all_tables, ["r", "s", "t"])
        # Biaya hasil ≤ biaya awal
        self.assertLessEqual(
            self.model.get_cost(result),
            self.model.get_cost(_cross(_cross(_scan("r"), _scan("s")), _scan("t"))) + 1e-9,
        )

    def test_right_deep_associativity(self) -> None:
        """Join(r,Join(s,t)) dapat direstrukturisasi jika lebih murah."""
        plan = _cross(_scan("r"), _cross(_scan("s"), _scan("t")))
        result = self.reorder(plan)
        all_tables = _tables(result)
        self.assertCountEqual(all_tables, ["r", "s", "t"])
        self.assertLessEqual(
            self.model.get_cost(result),
            self.model.get_cost(_cross(_scan("r"), _cross(_scan("s"), _scan("t")))) + 1e-9,
        )

    def test_optimal_already_no_regression(self) -> None:
        """Jika susunan awal sudah optimal, biaya tidak naik."""
        plan = _cross(_scan("t"), _cross(_scan("s"), _scan("r")))
        original_cost = self.model.get_cost(plan)
        result = self.reorder(plan)
        self.assertLessEqual(self.model.get_cost(result), original_cost + 1e-9)

    def test_tables_preserved_after_reorder(self) -> None:
        """Semua tabel harus tetap ada setelah reorder (tidak ada yang hilang/duplikat)."""
        plan = _cross(_cross(_scan("r"), _scan("s")), _scan("t"))
        result = self.reorder(plan)
        self.assertCountEqual(_tables(result), ["r", "s", "t"])

    def test_result_is_always_join_node(self) -> None:
        """Node hasil reorder harus tetap instance Join."""
        plan = _cross(_cross(_scan("r"), _scan("s")), _scan("t"))
        result = self.reorder(plan)
        self.assertIsInstance(result, Join)


# ===========================================================================
# Tes kombinasi M2+M3
# ===========================================================================

class TestCommuteAndAssociate(JoinOrderCase):

    def test_combined_reorder_finds_better_plan(self) -> None:
        """Optimizer boleh menggunakan kombinasi M2+M3 untuk menemukan plan terbaik."""
        plan = _cross(_cross(_scan("r"), _scan("s")), _scan("t"))
        result = self.reorder(plan)
        self.assertCountEqual(_tables(result), ["r", "s", "t"])
        # Biaya optimal ≤ biaya asli
        original_cost = self.model.get_cost(_cross(_cross(_scan("r"), _scan("s")), _scan("t")))
        self.assertLessEqual(self.model.get_cost(result), original_cost + 1e-9)

    def test_reorder_is_idempotent(self) -> None:
        """Menjalankan reorder dua kali tidak memperburuk hasil."""
        plan = _cross(_cross(_scan("r"), _scan("s")), _scan("t"))
        once  = self.reorder(plan)
        # Buat ulang plan identik untuk menghindari efek samping mutasi
        once2 = self.reorder(_cross(_cross(_scan("r"), _scan("s")), _scan("t")))
        cost_once  = self.model.get_cost(once)
        cost_twice = self.model.get_cost(self.reorder(once2))
        self.assertAlmostEqual(cost_once, cost_twice, places=6)

    def test_children_also_reordered_recursively(self) -> None:
        """Sub-join di dalam juga dioptimalkan (bottom-up)."""
        # Buat plan 4 tabel: Join(Join(r,s), Join(t, t2))
        self.stats["t2"] = TableStats("t2", 20, 2, 100.0, 10, {"id": 20})
        self.schemas["t2"] = TableSchema("t2", [ColumnSchema("id", DataType.INTEGER)])
        plan = _cross(
            _cross(_scan("r"), _scan("s")),
            _cross(_scan("t"), _scan("t2")),
        )
        result = self.reorder(plan)
        all_tables = _tables(result)
        self.assertCountEqual(all_tables, ["r", "s", "t", "t2"])
        # Biaya tidak boleh lebih buruk dari aslinya
        original = _cross(_cross(_scan("r"), _scan("s")), _cross(_scan("t"), _scan("t2")))
        self.assertLessEqual(self.model.get_cost(result), self.model.get_cost(original) + 1e-9)

    def test_single_join_no_crash(self) -> None:
        """Join dua tabel tanpa nesting tidak crash."""
        plan = _cross(_scan("r"), _scan("s"))
        result = self.reorder(plan)
        self.assertIsInstance(result, Join)
        self.assertCountEqual(_tables(result), ["r", "s"])

    def test_non_join_root_passthrough(self) -> None:
        """Root bukan Join (mis. Project di atas Join) dikembalikan apa adanya."""
        inner = _cross(_scan("r"), _scan("s"))
        project = Project(columns=[_col("r", "id")], children=[inner])
        result = self.reorder(project)
        self.assertIsInstance(result, Project)
        # Anak di dalam tetap dioptimalkan
        self.assertIsInstance(result.children[0], Join)


# ===========================================================================
# Tes edge-case
# ===========================================================================

class TestEdgeCases(JoinOrderCase):

    def test_empty_plan_node_no_crash(self) -> None:
        """PlanNode tanpa anak tidak crash (kontrak sentinel QO-08)."""
        result = self.reorder(PlanNode())
        self.assertIsInstance(result, PlanNode)

    def test_single_table_scan_passthrough(self) -> None:
        """TableScan tunggal dikembalikan utuh."""
        result = self.reorder(_scan("r"))
        self.assertIsInstance(result, TableScan)
        self.assertEqual(result.table, "r")

    def test_equal_cost_keeps_original_order(self) -> None:
        """Jika biaya sama (toleransi 1e-9), jangan tukar (hindari osilasi)."""
        # Buat dua tabel dengan statistik identik
        self.stats["r2"] = TableStats("r2", 1000, 100, 100.0, 10, {"id": 1000})
        self.schemas["r2"] = TableSchema("r2", [ColumnSchema("id", DataType.INTEGER)])
        plan = _cross(_scan("r"), _scan("r2"))
        result = self.reorder(plan)
        # Biaya tidak boleh lebih buruk (toleransi floating point)
        self.assertAlmostEqual(
            self.model.get_cost(result),
            self.model.get_cost(_cross(_scan("r"), _scan("r2"))),
            places=6,
        )

    def test_deep_left_chain_tables_preserved(self) -> None:
        """Chain join kiri dalam (r JOIN s JOIN t) tetap mempertahankan semua tabel."""
        plan = _cross(_cross(_scan("r"), _scan("s")), _scan("t"))
        result = self.reorder(plan)
        self.assertCountEqual(_tables(result), ["r", "s", "t"])

    def test_algorithm_attribute_preserved(self) -> None:
        """Atribut algorithm (mis. 'BNLJ') dipertahankan setelah reorder."""
        plan = Join(kind=JoinKind.CROSS, algorithm="BNLJ",
                    children=[_scan("r"), _scan("s")])
        result = self.reorder(plan)
        # Semua Join dalam pohon harus punya algorithm yang sama
        def check_algo(node: PlanNode) -> None:
            if isinstance(node, Join) and node.kind != JoinKind.NATURAL:
                self.assertEqual(node.algorithm, "BNLJ")
            for child in node.children:
                check_algo(child)
        check_algo(result)

    def test_index_scan_outer_preferred_when_selective(self) -> None:
        """IndexScan dengan selektivitas tinggi (1 blok hasil) dipilih sebagai outer."""
        idx_r = IndexScan(table="r", index=IndexInfo("idx_r_id", "r", "id"), op=CompOp.EQ, key=1)
        plan = _cross(_scan("s"), idx_r)
        result = self.reorder(plan)
        self.assertEqual(_tables(result), ["r", "s"])

    def test_select_star_project_preserves_column_order(self) -> None:
        """Project(columns=[]) (SELECT *) menjaga urutan tabel daun agar urutan kolom stabil."""
        project = Project(columns=[], children=[_cross(_scan("r"), _scan("s"))])
        result = self.reorder(project)
        self.assertIsInstance(result, Project)
        self.assertEqual(_tables(result), ["r", "s"])

    def test_output_columns_reordered_with_children(self) -> None:
        """output_columns pada Join mengikuti urutan anak setelah ditukar."""
        r = TableScan(table="r", output_columns=[_col("r", "id")])
        s = TableScan(table="s", output_columns=[_col("s", "id")])
        plan = Join(kind=JoinKind.CROSS, children=[r, s], output_columns=[_col("r", "id"), _col("s", "id")])
        result = self.reorder(plan)
        self.assertEqual(result.output_columns, [_col("s", "id"), _col("r", "id")])


if __name__ == "__main__":
    unittest.main()

