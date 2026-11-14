from __future__ import annotations

import unittest
from unittest.mock import Mock, call, patch

from common.catalog import DataType, IndexInfo, IndexKind
from common.errors import StorageError, TransactionAbortedError
from common.expression import ColumnRef, Comparison, CompOp, Literal
from common.plan import IndexScan
from common.storage import RID, Row
from common.transaction import AccessResponse, AccessStatus, Action
from query_processor.context import ExecContext
from query_processor.errors import PlanError
from query_processor.operators.base import RowOperator
from query_processor.operators.create_index import CreateIndexExecution
from query_processor.operators.filter import FilterOp
from query_processor.operators.scan import IndexScanOp
from query_processor.translator import PlanTranslator
from query_processor.tuple import ColumnBinding, ExecRow, OutputSchema, Value


class FakeIndexSM:
    def __init__(self) -> None:
        self.rows = {
            RID("t", 0, i): Row(RID("t", 0, i), [value, i])
            for i, value in enumerate([1, 2, 2, 3, 4])
        }
        self.buckets = {}
        for rid, row in self.rows.items():
            self.buckets.setdefault(row.values[0], []).append(rid)
        self.created = []
        self.error = None

    def create_index(self, info: IndexInfo) -> None:
        if self.error:
            raise self.error
        self.created.append(info)

    def index_lookup(self, table: str, column: str, key: Value) -> list[RID]:
        if self.error:
            raise self.error
        return list(self.buckets.get(key, []))

    def index_range(self, table: str, column: str, low: Value, high: Value) -> list[RID]:
        return [
            rid
            for key, rids in self.buckets.items()
            if (low is None or low <= key) and (high is None or key <= high)
            for rid in rids
        ]

    def fetch(self, table: str, rid: RID) -> Row | None:
        if self.error:
            raise self.error
        return self.rows.get(rid)

    def scan(self, table: str) -> None:
        raise AssertionError("IndexScan must not call scan")


class TestIndexOperators(unittest.TestCase):
    def setUp(self) -> None:
        self.sm = FakeIndexSM()
        self.calls = Mock()
        for name in ("create_index", "index_lookup", "index_range", "fetch"):
            method = Mock(wraps=getattr(self.sm, name))
            setattr(self.sm, name, method)
            self.calls.attach_mock(method, name)

        self.ctx = ExecContext(tx_id=7, sm=self.sm, ccm=Mock())
        self.ctx.authorize_table = self.calls.authorize_table
        self.ctx.authorize_rid = self.calls.authorize_rid
        self.ctx.authorize_table.return_value = AccessResponse(AccessStatus.ALLOWED)
        self.ctx.authorize_rid.return_value = AccessResponse(AccessStatus.ALLOWED)
        self.info = IndexInfo("idx", "t", "key")
        self.schema = OutputSchema([
            ColumnBinding("t", "key", DataType.INTEGER),
            ColumnBinding("t", "id", DataType.INTEGER),
        ])

    def scan(self, op: CompOp = CompOp.EQ, **kwargs) -> IndexScanOp:
        return IndexScanOp("t", self.info, op, self.schema, **kwargs)

    def collect(self, op: RowOperator) -> list[ExecRow]:
        op.open(self.ctx)
        try:
            result = []
            while (row := op.next()) is not None:
                result.append(row)
            return result
        finally:
            op.close()

    def test_create_forwards_metadata_and_reports_success_for_both_kinds(self) -> None:
        for kind in IndexKind:
            with self.subTest(kind=kind):
                self.info.kind = kind
                result = CreateIndexExecution(self.info).execute(self.ctx)
                self.assertTrue(result.ok)
                self.assertIs(self.sm.created[-1], self.info)
                self.assertIn("idx", result.message)
                self.assertIn("t(key)", result.message)
                self.assertEqual(self.calls.mock_calls[-2], call.authorize_table("t"))

    def test_create_propagates_storage_failure(self) -> None:
        self.sm.error = StorageError("creation failed")
        with self.assertRaisesRegex(StorageError, "creation failed"):
            CreateIndexExecution(self.info).execute(self.ctx)
        self.assertEqual(self.sm.created, [])

    def test_create_waits_before_build_and_aborted_never_builds(self) -> None:
        self.ctx.authorize_table.side_effect = [
            AccessResponse(AccessStatus.WAIT),
            AccessResponse(AccessStatus.ALLOWED),
            AccessResponse(AccessStatus.ABORTED),
        ]
        with patch("query_processor.operators.create_index.sleep") as sleep:
            CreateIndexExecution(self.info).execute(self.ctx)
        sleep.assert_called_once()
        self.assertEqual(
            self.calls.mock_calls,
            [call.authorize_table("t"), call.authorize_table("t"), call.create_index(self.info)],
        )
        with self.assertRaises(TransactionAbortedError):
            CreateIndexExecution(self.info).execute(self.ctx)
        self.assertEqual(len(self.sm.created), 1)

    def test_equality_preserves_duplicate_keys_schema_and_rid(self) -> None:
        for kind in IndexKind:
            self.info.kind = kind
            rows = self.collect(self.scan(key=2))
            self.assertEqual([r.values for r in rows], [(2, 1), (2, 2)])
            self.assertEqual([r.rid for r in rows], [RID("t", 0, 1), RID("t", 0, 2)])
            self.assertTrue(all(r.schema is self.schema for r in rows))

    def test_ranges_match_full_data_filter_including_strict_boundaries(self) -> None:
        cases = [
            (CompOp.GT, {"low": 2}, lambda x: x > 2),
            (CompOp.GE, {"low": 2}, lambda x: x >= 2),
            (CompOp.LT, {"high": 2}, lambda x: x < 2),
            (CompOp.LE, {"high": 2}, lambda x: x <= 2),
            (CompOp.GT, {"low": 1, "high": 3}, lambda x: 1 < x <= 3),
            (CompOp.LT, {"low": 1, "high": 3}, lambda x: 1 <= x < 3),
            (CompOp.GE, {"low": 4, "high": 1}, lambda x: False),
        ]
        for op, bounds, predicate in cases:
            with self.subTest(op=op, bounds=bounds):
                actual = self.collect(self.scan(op, **bounds))
                expected = [tuple(r.values) for r in self.sm.rows.values() if predicate(r.values[0])]
                self.assertCountEqual([r.values for r in actual], expected)

    def test_empty_missing_and_null_keys(self) -> None:
        for key in (99, None):
            self.assertEqual(self.collect(self.scan(key=key)), [])
        self.sm.buckets.clear()
        self.assertEqual(self.collect(self.scan(CompOp.GE, low=0)), [])

    def test_residual_filter_is_applied(self) -> None:
        condition = Comparison(CompOp.GT, ColumnRef("t", "id"), Literal(1, DataType.INTEGER))
        rows = self.collect(FilterOp(self.scan(key=2), condition))
        self.assertEqual([r.values for r in rows], [(2, 2)])

    def test_wait_retries_identical_request_before_lookup_and_fetch(self) -> None:
        self.ctx.authorize_table.side_effect = [
            AccessResponse(AccessStatus.WAIT), AccessResponse(AccessStatus.ALLOWED),
        ]
        self.ctx.authorize_rid.side_effect = [
            AccessResponse(AccessStatus.WAIT), AccessResponse(AccessStatus.ALLOWED),
        ]
        with patch("query_processor.operators.scan.sleep") as sleep:
            self.collect(self.scan(key=1))

        self.assertEqual(sleep.call_count, 2)
        rid = RID("t", 0, 0)
        self.assertEqual(self.calls.mock_calls, [
            call.authorize_table("t"),
            call.authorize_table("t"),
            call.index_lookup("t", "key", 1),
            call.authorize_rid("t", rid, Action.READ),
            call.authorize_rid("t", rid, Action.READ),
            call.fetch("t", rid),
        ])

    def test_abort_before_lookup_or_fetch_stops_operator(self) -> None:
        for table_status in (AccessStatus.ABORTED, AccessStatus.ALLOWED):
            with self.subTest(table_status=table_status):
                self.calls.reset_mock()
                self.ctx.authorize_table.return_value = AccessResponse(table_status)
                self.ctx.authorize_rid.side_effect = [
                    AccessResponse(AccessStatus.WAIT), AccessResponse(AccessStatus.ABORTED),
                ]
                op = self.scan(key=2)

                with patch("query_processor.operators.scan.sleep"):
                    with self.assertRaises(TransactionAbortedError):
                        op.open(self.ctx)
                        op.next()

                self.sm.fetch.assert_not_called()
                if table_status == AccessStatus.ABORTED:
                    self.sm.index_lookup.assert_not_called()
                self.assertIsNone(op.next())

    def test_missing_or_changed_record_is_rechecked(self) -> None:
        del self.sm.rows[RID("t", 0, 1)]
        self.sm.rows[RID("t", 0, 2)].values[0] = 9
        self.assertEqual(self.collect(self.scan(key=2)), [])

    def test_close_reopen_and_failure_clear_state(self) -> None:
        op = self.scan(key=1)
        self.assertIsNone(op.next())
        self.assertEqual(len(self.collect(op)), 1)
        self.assertIsNone(op.next())
        op.open(self.ctx)
        self.sm.error = StorageError("fetch failed")
        with self.assertRaises(StorageError):
            op.next()
        self.assertIsNone(op.next())
        with self.assertRaises(StorageError):
            op.open(self.ctx)
        self.assertIsNone(op.next())
        self.sm.error = None
        self.assertEqual(len(self.collect(op)), 1)

    def test_translator_rejects_invalid_search_without_storage_access(self) -> None:
        translator = PlanTranslator()
        for op, kwargs in [
            (CompOp.NE, {"key": 2}),
            (CompOp.GT, {}),
            (CompOp.EQ, {"key": 1, "low": 0}),
        ]:
            with self.assertRaises(PlanError):
                translator.build_rows(
                    IndexScan(table="t", index=self.info, op=op, **kwargs), self.ctx
                )
        self.info.kind = IndexKind.HASH
        with self.assertRaises(PlanError):
            translator.build_rows(
                IndexScan(table="t", index=self.info, op=CompOp.GE, low=1), self.ctx
            )
        self.assertEqual(self.calls.mock_calls, [])


if __name__ == "__main__":
    unittest.main()
