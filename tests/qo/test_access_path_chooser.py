"""QO-13: unit test AccessPathChooser (pemilihan TableScan vs IndexScan)."""

from __future__ import annotations

import unittest
from unittest.mock import create_autospec

from common.catalog import ColumnSchema, DataType, IndexInfo, IndexKind, TableSchema, TableStats
from common.expression import And, ColumnRef, Comparison, CompOp, Literal
from common.interfaces import StorageManager
from common.plan import (
    Delete, Filter, IndexScan, Join, JoinKind, PlanNode, Project, Sort, TableScan, Update,
)
from query_optimizer.access_path_chooser import AccessPathChooser
from query_optimizer.binder import Binder
from query_optimizer.cost_model import CostModel
from query_optimizer.optimizer import QueryOptimizerFacade


def _col(table: str | None, column: str) -> ColumnRef:
    return ColumnRef(table=table, column=column)


def _lit_int(val: int | None) -> Literal:
    return Literal(value=val, type=DataType.INTEGER)


def _lit_str(val: str) -> Literal:
    return Literal(value=val, type=DataType.VARCHAR)


def _cmp(op: CompOp, left, right) -> Comparison:
    return Comparison(op=op, left=left, right=right)


class AccessPathCase(unittest.TestCase):
    """Fixture tabel r (1000 baris, 100 blok) dan s (500 baris, 50 blok)."""

    def setUp(self) -> None:
        self.idx_r_id = IndexInfo("idx_r_id", "r", "id", IndexKind.BPLUS_TREE)
        self.idx_r_group = IndexInfo("idx_r_group", "r", "group_id", IndexKind.BPLUS_TREE)
        self.idx_s_hash = IndexInfo("idx_s_id_hash", "s", "id", IndexKind.HASH)

        self.stats = {
            "r": TableStats("r", 1000, 100, 100.0, 10, {"id": 1000, "group_id": 100, "note": 50}),
            "s": TableStats("s", 500, 50, 100.0, 10, {"id": 500, "value": 10}),
        }
        self.schemas = {
            "r": TableSchema(
                "r",
                [
                    ColumnSchema("id", DataType.INTEGER),
                    ColumnSchema("group_id", DataType.INTEGER),
                    ColumnSchema("note", DataType.VARCHAR, 30),
                ],
                primary_key=["id"],
                indexes=[self.idx_r_id],
            ),
            "s": TableSchema(
                "s",
                [
                    ColumnSchema("id", DataType.INTEGER),
                    ColumnSchema("value", DataType.INTEGER),
                ],
                primary_key=["id"],
                indexes=[self.idx_s_hash],
            ),
        }
        self.sm = create_autospec(StorageManager, instance=True, spec_set=True)
        self.sm.get_stats.side_effect = self.stats.__getitem__
        self.sm.get_schema.side_effect = self.schemas.__getitem__

        self.model = CostModel(self.sm, buffer_frames=12, block_size=1000)
        self.chooser = AccessPathChooser()

    def choose(self, plan: PlanNode) -> PlanNode:
        return self.chooser.choose(plan, self.model)


class TestEqualityIndexScan(AccessPathCase):
    def test_btree_equality_replaces_filter_tablescan(self) -> None:
        plan = Filter(condition=_cmp(CompOp.EQ, _col("r", "id"), _lit_int(42)), children=[TableScan(table="r")])
        result = self.choose(plan)
        self.assertIsInstance(result, IndexScan)
        assert isinstance(result, IndexScan)
        self.assertEqual(result.table, "r")
        self.assertEqual(result.index, self.idx_r_id)
        self.assertEqual(result.op, CompOp.EQ)
        self.assertEqual(result.key, 42)
        self.assertIsNone(result.low)
        self.assertIsNone(result.high)
        self.assertLess(self.model.get_cost(result), self.model.get_cost(plan))

    def test_reversed_equality_literal_left_supported(self) -> None:
        plan = Filter(condition=_cmp(CompOp.EQ, _lit_int(7), _col("r", "id")), children=[TableScan(table="r")])
        result = self.choose(plan)
        self.assertIsInstance(result, IndexScan)
        assert isinstance(result, IndexScan)
        self.assertEqual(result.op, CompOp.EQ)
        self.assertEqual(result.key, 7)

    def test_hash_index_supports_equality(self) -> None:
        plan = Filter(condition=_cmp(CompOp.EQ, _col("s", "id"), _lit_int(10)), children=[TableScan(table="s")])
        result = self.choose(plan)
        self.assertIsInstance(result, IndexScan)
        assert isinstance(result, IndexScan)
        self.assertEqual(result.index, self.idx_s_hash)
        self.assertEqual(result.op, CompOp.EQ)
        self.assertEqual(result.key, 10)

    def test_unqualified_column_ref_matches_scan_table(self) -> None:
        plan = Filter(condition=_cmp(CompOp.EQ, _col(None, "id"), _lit_int(5)), children=[TableScan(table="r")])
        result = self.choose(plan)
        self.assertIsInstance(result, IndexScan)


class TestRangeIndexScan(AccessPathCase):
    def test_btree_greater_than_and_greater_equal(self) -> None:
        for op in (CompOp.GT, CompOp.GE):
            with self.subTest(op=op):
                plan = Filter(condition=_cmp(op, _col("r", "id"), _lit_int(100)), children=[TableScan(table="r")])
                result = self.choose(plan)
                self.assertIsInstance(result, IndexScan)
                assert isinstance(result, IndexScan)
                self.assertEqual(result.op, op)
                self.assertEqual(result.low, 100)
                self.assertIsNone(result.high)
                self.assertIsNone(result.key)

    def test_btree_less_than_and_less_equal(self) -> None:
        for op in (CompOp.LT, CompOp.LE):
            with self.subTest(op=op):
                plan = Filter(condition=_cmp(op, _col("r", "id"), _lit_int(200)), children=[TableScan(table="r")])
                result = self.choose(plan)
                self.assertIsInstance(result, IndexScan)
                assert isinstance(result, IndexScan)
                self.assertEqual(result.op, op)
                self.assertEqual(result.high, 200)
                self.assertIsNone(result.low)
                self.assertIsNone(result.key)

    def test_reversed_range_flips_operator_direction(self) -> None:
        # 10 < r.id  <=>  r.id > 10
        plan_gt = Filter(condition=_cmp(CompOp.LT, _lit_int(10), _col("r", "id")), children=[TableScan(table="r")])
        res_gt = self.choose(plan_gt)
        self.assertIsInstance(res_gt, IndexScan)
        assert isinstance(res_gt, IndexScan)
        self.assertEqual(res_gt.op, CompOp.GT)
        self.assertEqual(res_gt.low, 10)
        self.assertIsNone(res_gt.high)

        # 50 >= r.id  <=>  r.id <= 50
        plan_le = Filter(condition=_cmp(CompOp.GE, _lit_int(50), _col("r", "id")), children=[TableScan(table="r")])
        res_le = self.choose(plan_le)
        self.assertIsInstance(res_le, IndexScan)
        assert isinstance(res_le, IndexScan)
        self.assertEqual(res_le.op, CompOp.LE)
        self.assertEqual(res_le.high, 50)
        self.assertIsNone(res_le.low)

    def test_bounded_range_combines_low_and_high_on_btree(self) -> None:
        cond = And(conditions=[
            _cmp(CompOp.GE, _col("r", "id"), _lit_int(100)),
            _cmp(CompOp.LE, _col("r", "id"), _lit_int(200)),
        ])
        plan = Filter(condition=cond, children=[TableScan(table="r")])
        result = self.choose(plan)
        self.assertIsInstance(result, IndexScan)
        assert isinstance(result, IndexScan)
        self.assertEqual(result.op, CompOp.GE)
        self.assertEqual(result.low, 100)
        self.assertEqual(result.high, 200)

    def test_hash_index_rejects_range_predicates(self) -> None:
        for op in (CompOp.GT, CompOp.GE, CompOp.LT, CompOp.LE):
            with self.subTest(op=op):
                plan = Filter(condition=_cmp(op, _col("s", "id"), _lit_int(10)), children=[TableScan(table="s")])
                result = self.choose(plan)
                self.assertIsInstance(result, Filter)
                self.assertIsInstance(result.children[0], TableScan)


class TestResidualAndMultipleIndexes(AccessPathCase):
    def test_remaining_predicate_wrapped_in_filter(self) -> None:
        p_idx = _cmp(CompOp.EQ, _col("r", "id"), _lit_int(1))
        p_rest = _cmp(CompOp.EQ, _col("r", "note"), _lit_str("aktif"))
        plan = Filter(condition=And(conditions=[p_idx, p_rest]), children=[TableScan(table="r")])
        result = self.choose(plan)
        self.assertIsInstance(result, Filter)
        assert isinstance(result, Filter)
        self.assertEqual(result.condition, p_rest)
        self.assertIsInstance(result.children[0], IndexScan)
        self.assertEqual(result.children[0].key, 1)

    def test_chained_filters_from_selection_rewriter_supported(self) -> None:
        p_idx = _cmp(CompOp.EQ, _col("r", "id"), _lit_int(1))
        p_rest = _cmp(CompOp.EQ, _col("r", "note"), _lit_str("aktif"))
        # Bahkan ketika Filter berindeks berada di atas Filter tak-berindeks
        chained = Filter(condition=p_idx, children=[Filter(condition=p_rest, children=[TableScan(table="r")])])
        result = self.choose(chained)
        self.assertIsInstance(result, Filter)
        self.assertEqual(result.condition, p_rest)
        self.assertIsInstance(result.children[0], IndexScan)

    def test_chooses_more_selective_index_when_multiple_indexes_match(self) -> None:
        self.schemas["r"].indexes = [self.idx_r_group, self.idx_r_id]
        p_group = _cmp(CompOp.EQ, _col("r", "group_id"), _lit_int(5))
        p_id = _cmp(CompOp.EQ, _col("r", "id"), _lit_int(99))
        plan = Filter(condition=And(conditions=[p_group, p_id]), children=[TableScan(table="r")])
        result = self.choose(plan)
        self.assertIsInstance(result, Filter)
        self.assertIsInstance(result.children[0], IndexScan)
        self.assertEqual(result.children[0].index, self.idx_r_id)
        self.assertEqual(result.condition, p_group)


class TestFallbackAndEdgeCases(AccessPathCase):
    def test_not_equal_keeps_table_scan(self) -> None:
        plan = Filter(condition=_cmp(CompOp.NE, _col("r", "id"), _lit_int(1)), children=[TableScan(table="r")])
        result = self.choose(plan)
        self.assertIsInstance(result, Filter)
        self.assertIsInstance(result.children[0], TableScan)

    def test_unindexed_column_keeps_table_scan(self) -> None:
        plan = Filter(condition=_cmp(CompOp.EQ, _col("r", "note"), _lit_str("abc")), children=[TableScan(table="r")])
        result = self.choose(plan)
        self.assertIsInstance(result, Filter)
        self.assertIsInstance(result.children[0], TableScan)

    def test_table_without_indexes_keeps_table_scan(self) -> None:
        self.schemas["r"].indexes = []
        plan = Filter(condition=_cmp(CompOp.EQ, _col("r", "id"), _lit_int(1)), children=[TableScan(table="r")])
        result = self.choose(plan)
        self.assertIsInstance(result, Filter)
        self.assertIsInstance(result.children[0], TableScan)

    def test_not_replaced_when_table_scan_is_already_cheaper_or_equal(self) -> None:
        # Tabel 1 blok: TableScan = 1 blok, IndexScan = 1 blok -> tetap TableScan
        self.stats["r"] = TableStats("r", 5, 1, 100.0, 10, {"id": 5, "group_id": 5, "note": 5})
        plan = Filter(condition=_cmp(CompOp.EQ, _col("r", "id"), _lit_int(1)), children=[TableScan(table="r")])
        result = self.choose(plan)
        self.assertIsInstance(result, Filter)
        self.assertIsInstance(result.children[0], TableScan)

    def test_null_or_incompatible_literal_keeps_table_scan(self) -> None:
        for lit in (_lit_int(None), _lit_str("bukan_angka")):
            with self.subTest(lit=lit):
                plan = Filter(condition=_cmp(CompOp.EQ, _col("r", "id"), lit), children=[TableScan(table="r")])
                result = self.choose(plan)
                self.assertIsInstance(result, Filter)
                self.assertIsInstance(result.children[0], TableScan)

    def test_alias_and_output_columns_preserved_on_index_scan(self) -> None:
        out_cols = [_col("r1", "id"), _col("r1", "group_id"), _col("r1", "note")]
        scan = TableScan(table="r", alias="r1", output_columns=out_cols)
        plan = Filter(
            condition=_cmp(CompOp.EQ, _col("r1", "id"), _lit_int(10)),
            children=[scan],
            output_columns=out_cols,
        )
        result = self.choose(plan)
        self.assertIsInstance(result, IndexScan)
        assert isinstance(result, IndexScan)
        self.assertEqual(result.alias, "r1")
        self.assertEqual(result.output_columns, out_cols)

    def test_input_plan_is_not_mutated(self) -> None:
        plan = Filter(condition=_cmp(CompOp.EQ, _col("r", "id"), _lit_int(1)), children=[TableScan(table="r")])
        _ = self.choose(plan)
        self.assertIsInstance(plan.children[0], TableScan)

    def test_nested_in_join_project_sort_update_delete_and_validates_with_binder(self) -> None:
        qo = QueryOptimizerFacade(self.sm)
        for sql in (
            "SELECT name FROM r WHERE id = 1;",
            "SELECT r.id, s.value FROM r, s WHERE r.id = s.id AND r.id = 1;",
            "UPDATE r SET group_id = 2 WHERE id = 1;",
            "DELETE FROM r WHERE id = 1;",
        ):
            if "name" in sql:
                sql = "SELECT note FROM r WHERE id = 1 ORDER BY group_id ASC;"
            with self.subTest(sql=sql):
                parsed = qo.parse_query(sql)
                optimized = qo.optimize_query(parsed)
                # Pastikan Binder menerima hasil optimasi dan ada IndexScan di pohon plan
                Binder(self.sm).bind(optimized)
                nodes = [optimized.plan]
                found_index_scan = False
                while nodes:
                    curr = nodes.pop()
                    if isinstance(curr, IndexScan):
                        found_index_scan = True
                    if curr is not None:
                        nodes.extend(curr.children)
                self.assertTrue(found_index_scan, f"IndexScan tidak ditemukan untuk query: {sql}")


if __name__ == "__main__":
    unittest.main()

