from __future__ import annotations

import copy
import unittest
from unittest.mock import create_autospec, patch

from common import config
from common.catalog import ColumnSchema, DataType, IndexInfo, TableSchema, TableStats
from common.errors import SemanticError, StorageError
from common.expression import And, ColumnRef, Comparison, CompOp, Literal
from common.interfaces import StorageManager
from common.plan import Filter, IndexScan, Insert, Join, JoinKind, PlanNode, Project, Sort, TableScan
from query_optimizer.cost_model import CostModel, estimate_cost
from query_optimizer.optimizer import QueryOptimizerFacade


def integer(value: int = 1) -> Literal:
    return Literal(value, DataType.INTEGER)


def predicate(table: str = "r", column: str = "group_id", op: CompOp = CompOp.EQ) -> Comparison:
    return Comparison(op, ColumnRef(table, column), integer())


class CostCase(unittest.TestCase):
    def setUp(self) -> None:
        self.stats = {
            "r": TableStats("r", 1000, 100, 100.0, 10, {"id": 1000, "group_id": 100}),
            "s": TableStats("s", 500, 50, 100.0, 10, {"id": 500, "value": 10}),
        }
        self.schemas = {
            table: TableSchema(table, [ColumnSchema(column, DataType.INTEGER) for column in stats.distinct])
            for table, stats in self.stats.items()
        }
        self.sm = create_autospec(StorageManager, instance=True, spec_set=True)

        def get_stats(table: str) -> TableStats:
            if table not in self.stats:
                raise SemanticError("Tabel tidak ada", table)
            return self.stats[table]

        self.sm.get_stats.side_effect = get_stats
        self.sm.get_schema.side_effect = self.schemas.__getitem__
        self.model = CostModel(self.sm)

    def tearDown(self) -> None:
        self.assertTrue(all(call[0] in ("get_stats", "get_schema") for call in self.sm.mock_calls))

    def filtered(self, condition=None, table: str = "r") -> Filter:
        return Filter(condition=condition if condition is not None else predicate(table), children=[TableScan(table=table)])


class TestScanCost(CostCase):
    def test_scan_counts_physical_blocks_and_annotates_rows(self) -> None:
        node = TableScan(table="r")
        self.assertEqual(self.model.get_cost(node), 100.0)
        self.assertEqual(node.estimated_cost, 100.0)
        self.assertEqual(node.estimated_rows, 1000.0)

    def test_estimate_returns_same_total_as_get_cost(self) -> None:
        self.assertEqual(self.model.estimate(TableScan(table="s")), 50.0)

    def test_scan_derives_missing_block_count_from_blocking_factor(self) -> None:
        self.stats["r"].b_r = 0
        self.assertEqual(self.model.get_cost(TableScan(table="r")), 100.0)

    def test_empty_table_has_zero_rows_but_can_have_allocated_blocks(self) -> None:
        self.stats["r"] = TableStats("r", 0, 3, 0.0, 0, {})
        node = TableScan(table="r")
        self.assertEqual(self.model.get_cost(node), 3.0)
        self.assertEqual(node.estimated_rows, 0.0)

    def test_fully_empty_statistics_do_not_divide_by_zero(self) -> None:
        self.stats["r"] = TableStats("r", 0, 0, 0.0, 0, {})
        node = self.filtered()
        self.assertEqual(self.model.get_cost(node), 0.0)
        self.assertEqual(node.estimated_rows, 0.0)

    def test_invalid_statistics_are_rejected(self) -> None:
        for field, value in (("n_r", -1), ("b_r", -1), ("f_r", -1), ("l_r", -1), ("l_r", float("nan"))):
            before = copy.deepcopy(self.stats["r"])
            setattr(self.stats["r"], field, value)
            with self.subTest(field=field, value=value), self.assertRaises(SemanticError):
                self.model.get_cost(TableScan(table="r"))
            self.stats["r"] = before

    def test_nonempty_stats_without_width_or_factor_are_rejected(self) -> None:
        self.stats["r"].l_r = 0
        self.stats["r"].f_r = 0
        with self.assertRaises(SemanticError):
            self.model.get_cost(TableScan(table="r"))

    def test_fresh_stats_and_plan_edits_ignore_old_annotations(self) -> None:
        node = self.filtered()
        self.model.get_cost(node)
        self.stats["r"].b_r = 200
        node.condition = predicate(column="id")
        node.estimated_cost = 99999
        node.estimated_rows = 99999
        self.assertEqual(self.model.get_cost(node), 200.0)
        self.assertEqual(node.estimated_rows, 1.0)

    def test_stats_and_schema_are_not_modified(self) -> None:
        before = copy.deepcopy((self.stats, self.schemas))
        self.model.get_cost(self.filtered())
        self.assertEqual((self.stats, self.schemas), before)

    def test_catalog_errors_propagate(self) -> None:
        class MissingTable(SemanticError):
            pass

        for error in (StorageError("broken catalog"), MissingTable("missing", "r")):
            self.sm.get_stats.side_effect = error
            with self.subTest(error=type(error).__name__), self.assertRaises(type(error)) as caught:
                self.model.get_cost(TableScan(table="r"))
            self.assertIs(caught.exception, error)

    def test_no_catalog_fails_explicitly(self) -> None:
        with self.assertRaises(SemanticError):
            CostModel().get_cost(TableScan(table="r"))

    def test_empty_base_plan_is_zero_cost_contract_sentinel(self) -> None:
        node = PlanNode(estimated_rows=123.0, estimated_cost=456.0)
        self.assertEqual(self.model.get_cost(node), 0.0)
        self.assertEqual(node.estimated_rows, 0.0)
        self.assertEqual(node.estimated_cost, 0.0)

    def test_unsupported_node_cannot_be_reported_as_free(self) -> None:
        for node in (
            PlanNode(children=[TableScan(table="r")]), Insert(table="r", values=[integer()]),
        ):
            with self.subTest(node=type(node).__name__), self.assertRaises(SemanticError):
                self.model.get_cost(node)

    def test_index_scan_compatibility_uses_indexed_column_distinct(self) -> None:
        for column, expected_rows in (("id", 1.0), ("group_id", 10.0)):
            node = IndexScan(table="r", index=IndexInfo("idx", "r", column), op=CompOp.EQ, key=1)
            with self.subTest(column=column):
                # Belum memodelkan traversal index M3: gunakan biaya scan konservatif.
                self.assertEqual(self.model.get_cost(node), 100.0)
                self.assertEqual(node.estimated_rows, expected_rows)

    def test_empty_index_scan_never_invents_one_matching_row(self) -> None:
        self.stats["r"] = TableStats("r", 0, 0, 0.0, 0, {})
        node = IndexScan(table="r", index=IndexInfo("idx", "r", "id"), op=CompOp.EQ, key=1)
        self.assertEqual(self.model.get_cost(node), 0.0)
        self.assertEqual(node.estimated_rows, 0.0)

    def test_range_index_scan_uses_shared_range_heuristic(self) -> None:
        node = IndexScan(table="r", index=IndexInfo("idx", "r", "id"), op=CompOp.GT, low=100)
        self.assertEqual(self.model.get_cost(node), 100.0)
        self.assertAlmostEqual(node.estimated_rows, 1000 / 3)

    def test_bounded_index_range_keeps_both_predicates(self) -> None:
        node = IndexScan(table="r", index=IndexInfo("idx", "r", "id"), op=CompOp.GE, low=100, high=200)
        self.assertEqual(self.model.get_cost(node), 100.0)
        self.assertAlmostEqual(node.estimated_rows, 1000 / 9)

    def test_invalid_index_scan_definition_is_rejected(self) -> None:
        for node in (
            IndexScan(table="r", index=IndexInfo("idx", "s", "id"), op=CompOp.EQ, key=1),
            IndexScan(table="r", index=IndexInfo("idx", "r", "missing"), op=CompOp.EQ, key=1),
            IndexScan(table="r", index=IndexInfo("idx", "r", "id"), op=CompOp.GT),
            IndexScan(table="r", index=IndexInfo("idx", "r", "id"), op=CompOp.NE, key=1),
        ):
            with self.subTest(node=node), self.assertRaises(SemanticError):
                self.model.get_cost(node)

    def test_bad_arity_and_plan_cycle_fail_explicitly(self) -> None:
        cycle = Project(columns=[])
        cycle.children = [cycle]
        for node in (Project(columns=[]), TableScan(table="r", children=[TableScan(table="s")]), cycle):
            with self.subTest(node=type(node).__name__), self.assertRaises(SemanticError):
                self.model.get_cost(node)


class TestFilterCost(CostCase):
    def test_equality_changes_rows_without_discounting_scan_cost(self) -> None:
        node = self.filtered()
        self.assertEqual(self.model.get_cost(node), 100.0)
        self.assertEqual(node.estimated_rows, 10.0)
        self.assertEqual(node.children[0].estimated_rows, 1000.0)

    def test_reverse_equality_has_same_selectivity(self) -> None:
        node = self.filtered(Comparison(CompOp.EQ, integer(), ColumnRef("r", "group_id")))
        self.model.get_cost(node)
        self.assertEqual(node.estimated_rows, 10.0)

    def test_not_equal_uses_complement(self) -> None:
        node = self.filtered(predicate(op=CompOp.NE))
        self.model.get_cost(node)
        self.assertEqual(node.estimated_rows, 990.0)

    def test_range_uses_heuristic_without_min_max(self) -> None:
        for op in (CompOp.LT, CompOp.LE, CompOp.GT, CompOp.GE):
            node = self.filtered(predicate(op=op))
            self.model.get_cost(node)
            self.assertAlmostEqual(node.estimated_rows, 1000 / 3)

    def test_independent_and_conditions_multiply_selectivities(self) -> None:
        node = self.filtered(And([predicate(), predicate(column="id")]))
        self.model.get_cost(node)
        self.assertAlmostEqual(node.estimated_rows, 0.01)
        self.assertEqual(node.estimated_cost, 100.0)

    def test_missing_or_zero_distinct_uses_unknown_equality_fallback(self) -> None:
        for distinct in ({"id": 1000}, {"id": 1000, "group_id": 0}):
            self.stats["r"].distinct = distinct
            node = self.filtered()
            self.model.get_cost(node)
            self.assertEqual(node.estimated_rows, 100.0)

    def test_distinct_larger_than_rows_is_clamped(self) -> None:
        self.stats["r"].distinct["group_id"] = 10000
        node = self.filtered()
        self.model.get_cost(node)
        self.assertEqual(node.estimated_rows, 1.0)

    def test_literal_comparisons_can_estimate_exact_zero_or_all_rows(self) -> None:
        for op, right, expected in ((CompOp.EQ, 1, 1000.0), (CompOp.EQ, 2, 0.0), (CompOp.LT, 2, 1000.0)):
            node = self.filtered(Comparison(op, integer(1), integer(right)))
            self.model.get_cost(node)
            self.assertEqual(node.estimated_rows, expected)

    def test_comparison_with_null_matches_no_rows(self) -> None:
        node = self.filtered(Comparison(CompOp.EQ, ColumnRef("r", "group_id"), Literal(None, DataType.INTEGER)))
        self.model.get_cost(node)
        self.assertEqual(node.estimated_rows, 0.0)

    def test_unknown_or_out_of_scope_column_is_rejected(self) -> None:
        for ref in (ColumnRef("s", "id"), ColumnRef("r", "missing")):
            with self.subTest(ref=ref), self.assertRaises(SemanticError):
                self.model.get_cost(self.filtered(Comparison(CompOp.EQ, ref, integer())))

    def test_invalid_and_cyclic_condition_fail_explicitly(self) -> None:
        cycle = And([])
        cycle.conditions = [cycle]
        for condition in (And([]), integer(), cycle):
            with self.subTest(condition=type(condition).__name__), self.assertRaises(SemanticError):
                self.model.get_cost(self.filtered(condition))

    def test_equality_distinct_propagates_transitively_independent_of_and_order(self) -> None:
        self.stats["r"] = TableStats("r", 1000000, 100000, 100.0, 10, {"a": 100, "b": 10, "c": 2})
        self.schemas["r"] = TableSchema("r", [ColumnSchema(name, DataType.INTEGER) for name in ("a", "b", "c")])
        conditions = [
            Comparison(CompOp.EQ, ColumnRef("r", "a"), ColumnRef("r", "b")),
            Comparison(CompOp.EQ, ColumnRef("r", "b"), ColumnRef("r", "c")),
        ]
        for ordered in (conditions, list(reversed(conditions))):
            inner = self.filtered(And(ordered))
            outer = Filter(condition=predicate(column="a"), children=[inner])
            self.model.get_cost(outer)
            self.assertEqual(inner.estimated_rows, 1000.0)
            self.assertEqual(outer.estimated_rows, 500.0)

    def test_literal_equality_ndv_propagates_to_equal_columns(self) -> None:
        self.stats["r"] = TableStats("r", 1000000, 100000, 100.0, 10, {"id": 100, "group_id": 10})
        conditions = [
            Comparison(CompOp.EQ, ColumnRef("r", "id"), ColumnRef("r", "group_id")),
            predicate(),
        ]
        for ordered in (conditions, list(reversed(conditions))):
            inner = self.filtered(And(ordered))
            outer = Filter(condition=predicate(column="id"), children=[inner])
            self.model.get_cost(outer)
            self.assertEqual(inner.estimated_rows, 1000.0)
            self.assertEqual(outer.estimated_rows, 1000.0)


class TestProjectionCost(CostCase):
    def test_projection_keeps_duplicate_rows_and_adds_no_io(self) -> None:
        node = Project(columns=[ColumnRef("r", "id")], children=[TableScan(table="r")])
        self.assertEqual(self.model.get_cost(node), 100.0)
        self.assertEqual(node.estimated_rows, 1000.0)

    def test_unexpanded_star_preserves_scan_shape(self) -> None:
        node = Project(columns=[], children=[TableScan(table="r")])
        self.assertEqual(self.model.get_cost(node), 100.0)
        self.assertEqual(node.estimated_rows, 1000.0)

    def test_projected_away_column_is_unavailable_to_filter(self) -> None:
        project = Project(columns=[ColumnRef("r", "id")], children=[TableScan(table="r")])
        with self.assertRaises(SemanticError):
            self.model.get_cost(Filter(condition=predicate(), children=[project]))


class TestJoinCost(CostCase):
    def join(self, left=None, right=None, *, condition=None, kind=JoinKind.CROSS) -> Join:
        return Join(kind=kind, condition=condition, children=[
            left if left is not None else TableScan(table="r"),
            right if right is not None else TableScan(table="s"),
        ])

    def configured_model(self) -> CostModel:
        return CostModel(self.sm, buffer_frames=12, block_size=1000)

    def test_bnlj_counts_outer_once_and_inner_once_per_chunk(self) -> None:
        node = self.join()
        self.assertEqual(self.configured_model().get_cost(node), 600.0)
        self.assertEqual(node.estimated_rows, 500000.0)
        self.assertEqual(node.children[0].estimated_cost, 100.0)
        self.assertEqual(node.children[1].estimated_cost, 50.0)

    def test_swapping_sides_changes_rescan_cost(self) -> None:
        node = self.join(left=TableScan(table="s"), right=TableScan(table="r"))
        self.assertEqual(self.configured_model().get_cost(node), 550.0)

    def test_equi_join_uses_max_distinct_for_selectivity(self) -> None:
        condition = Comparison(CompOp.EQ, ColumnRef("r", "id"), ColumnRef("s", "id"))
        node = self.join(condition=condition, kind=JoinKind.THETA)
        self.assertEqual(self.configured_model().get_cost(node), 600.0)
        self.assertEqual(node.estimated_rows, 500.0)

    def test_natural_join_uses_shared_columns_and_keeps_right_qualifier(self) -> None:
        natural = self.join(kind=JoinKind.NATURAL)
        filtered = Filter(condition=predicate(table="s", column="id"), children=[natural])
        self.assertEqual(self.configured_model().get_cost(filtered), 600.0)
        self.assertEqual(natural.estimated_rows, 500.0)
        self.assertEqual(filtered.estimated_rows, 1.0)

    def test_natural_join_without_common_columns_is_cross_join(self) -> None:
        self.schemas["s"].columns[0].name = "other_id"
        self.stats["s"].distinct = {"other_id": 500, "value": 10}
        node = self.join(kind=JoinKind.NATURAL)
        self.assertEqual(self.configured_model().get_cost(node), 600.0)
        self.assertEqual(node.estimated_rows, 500000.0)

    def test_outer_filter_pushdown_reduces_bnlj_chunks(self) -> None:
        model = self.configured_model()
        pushed = self.join(left=self.filtered())
        above = Filter(condition=predicate(), children=[self.join()])
        self.assertEqual(model.get_cost(pushed), 150.0)
        self.assertEqual(model.get_cost(above), 600.0)
        self.assertEqual(pushed.estimated_rows, above.estimated_rows)

    def test_projection_pushdown_reduces_outer_bytes(self) -> None:
        projected = Project(columns=[ColumnRef("r", "id")], children=[TableScan(table="r")])
        self.assertEqual(self.configured_model().get_cost(self.join(left=projected)), 350.0)

    def test_duplicate_projection_columns_still_occupy_space(self) -> None:
        projected = Project(columns=[ColumnRef("r", "id")] * 2, children=[TableScan(table="r")])
        self.assertEqual(self.configured_model().get_cost(self.join(left=projected)), 600.0)

    def test_filtered_inner_keeps_scan_io_on_every_rescan(self) -> None:
        node = self.join(right=self.filtered(predicate(table="s", column="value"), table="s"))
        self.assertEqual(self.configured_model().get_cost(node), 600.0)
        self.assertEqual(node.estimated_rows, 50000.0)

    def test_nested_inner_subtree_is_recomputed_per_outer_chunk(self) -> None:
        self.stats["t"] = TableStats("t", 100, 10, 100.0, 10, {"t_id": 100})
        self.schemas["t"] = TableSchema("t", [ColumnSchema("t_id", DataType.INTEGER)])
        inner = self.join(left=TableScan(table="s"), right=TableScan(table="t"))
        node = self.join(right=inner)
        self.assertEqual(self.configured_model().get_cost(node), 1100.0)
        self.assertEqual(inner.estimated_cost, 100.0)

    def test_zero_outer_rows_skip_inner_but_keep_outer_read(self) -> None:
        outer = self.filtered(Comparison(CompOp.EQ, integer(1), integer(2)))
        node = self.join(left=outer)
        self.assertEqual(self.configured_model().get_cost(node), 100.0)
        self.assertEqual(node.estimated_rows, 0.0)

    def test_empty_inner_returns_zero_rows_without_division_by_zero(self) -> None:
        self.stats["s"] = TableStats("s", 0, 0, 0.0, 0, {})
        node = self.join(kind=JoinKind.NATURAL)
        self.assertEqual(self.configured_model().get_cost(node), 100.0)
        self.assertEqual(node.estimated_rows, 0.0)

    def test_join_predicate_cannot_reference_unavailable_table(self) -> None:
        node = self.join(condition=predicate(table="outside"), kind=JoinKind.THETA)
        with self.assertRaises(SemanticError):
            self.configured_model().get_cost(node)

    def test_ambiguous_unqualified_column_is_rejected(self) -> None:
        node = Filter(condition=Comparison(CompOp.EQ, ColumnRef(None, "id"), integer()), children=[self.join()])
        with self.assertRaises(SemanticError):
            self.configured_model().get_cost(node)

    def test_unsupported_join_algorithm_fails_explicitly(self) -> None:
        node = self.join()
        node.algorithm = "HASH"
        with self.assertRaises(SemanticError):
            self.configured_model().get_cost(node)

    def test_nonempty_tuple_width_can_be_derived_from_factor(self) -> None:
        self.stats["r"].l_r = 0.0
        projected = Project(columns=[ColumnRef("r", "id")], children=[TableScan(table="r")])
        self.assertEqual(self.configured_model().get_cost(self.join(left=projected)), 350.0)

    def test_char_literal_padding_matches_qp_and_preserves_inner_rescans(self) -> None:
        for left, right in (
            (Literal("x ", DataType.CHAR), Literal("x", DataType.VARCHAR)),
            (Literal("x ", DataType.VARCHAR), Literal("x", DataType.CHAR)),
        ):
            outer = self.filtered(Comparison(CompOp.EQ, left, right))
            node = self.join(left=outer)
            self.assertEqual(self.configured_model().get_cost(node), 600.0)
            self.assertEqual(outer.estimated_rows, 1000.0)
        # VARCHAR-only comparisons preserve trailing spaces in QP.
        outer = self.filtered(Comparison(CompOp.EQ, Literal("x ", DataType.VARCHAR), Literal("x", DataType.VARCHAR)))
        self.assertEqual(self.configured_model().get_cost(self.join(left=outer)), 100.0)
        self.assertEqual(outer.estimated_rows, 0.0)


class TestSortCost(CostCase):
    def sort(self, child=None) -> Sort:
        return Sort(column=ColumnRef("r", "id"), children=[child if child is not None else TableScan(table="r")])

    def test_in_memory_sort_adds_no_block_io(self) -> None:
        self.stats["r"].n_r = 120
        self.stats["r"].b_r = 12
        node = self.sort()
        self.assertEqual(CostModel(self.sm, buffer_frames=12, block_size=1000).get_cost(node), 12.0)
        self.assertEqual(node.estimated_rows, 120.0)

    def test_sort_spill_counts_run_write_and_final_merge_read(self) -> None:
        node = self.sort()
        self.assertEqual(CostModel(self.sm, buffer_frames=12, block_size=1000).get_cost(node), 300.0)

    def test_multiple_merge_passes_and_pass_boundary(self) -> None:
        for blocks, expected in ((20, 60.0), (21, 105.0)):
            self.stats["r"].b_r = blocks
            self.stats["r"].n_r = blocks * 10
            node = self.sort()
            with self.subTest(blocks=blocks):
                self.assertEqual(CostModel(self.sm, buffer_frames=5, block_size=1000).get_cost(node), expected)

    def test_filter_before_sort_can_avoid_spilling(self) -> None:
        node = self.sort(self.filtered())
        self.assertEqual(CostModel(self.sm, buffer_frames=12, block_size=1000).get_cost(node), 100.0)
        self.assertEqual(node.estimated_rows, 10.0)

    def test_projection_before_sort_reduces_spill_volume(self) -> None:
        project = Project(columns=[ColumnRef("r", "id")], children=[TableScan(table="r")])
        self.assertEqual(CostModel(self.sm, buffer_frames=12, block_size=1000).get_cost(self.sort(project)), 200.0)

    def test_empty_result_does_not_spill_allocated_empty_pages(self) -> None:
        self.stats["r"] = TableStats("r", 0, 100, 0.0, 0, {})
        self.assertEqual(CostModel(self.sm, buffer_frames=12).get_cost(self.sort()), 100.0)


class TestPublicCostAPI(CostCase):
    def test_helper_accepts_catalog_and_physical_parameters(self) -> None:
        node = Sort(column=ColumnRef("r", "id"), children=[TableScan(table="r")])
        self.assertEqual(estimate_cost(node, self.sm, buffer_frames=12, block_size=1000), 300.0)

    def test_helper_without_catalog_fails_explicitly(self) -> None:
        with self.assertRaises(SemanticError):
            estimate_cost(TableScan(table="r"))

    def test_facade_get_cost_delegates_to_real_cost_model(self) -> None:
        node = self.filtered()
        self.assertEqual(QueryOptimizerFacade(self.sm).get_cost(node), 100.0)
        self.assertEqual(node.estimated_rows, 10.0)

    def test_defaults_read_common_config_when_model_is_created(self) -> None:
        with patch.object(config, "BUFFER_FRAMES", 12), patch.object(config, "BLOCK_SIZE", 1000):
            model = CostModel(self.sm)
        node = Join(kind=JoinKind.CROSS, children=[TableScan(table="r"), TableScan(table="s")])
        self.assertEqual(model.get_cost(node), 600.0)

    def test_invalid_physical_parameters_fail_at_construction(self) -> None:
        for kwargs in ({"buffer_frames": 2}, {"buffer_frames": True}, {"block_size": 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                CostModel(self.sm, **kwargs)


if __name__ == "__main__":
    unittest.main()
