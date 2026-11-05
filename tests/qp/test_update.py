from __future__ import annotations

import unittest
from unittest.mock import Mock

from common.catalog import DataType
from common.errors import SchemaViolationError, TransactionAbortedError
from common.expression import ArithOp, BinaryOp, ColumnRef, Comparison, CompOp, Literal
from common.storage import RID, Row
from common.transaction import AccessStatus, Action, ChangeOp
from query_processor.operators.filter import FilterOp
from query_processor.operators.update import UpdateExecution
from tests.qp.fakes import ROWS, FakeCtx, FakeSM, FakeSource


class UpdateSM(FakeSM):
    """SM tiruan untuk tes UPDATE."""

    def __init__(self) -> None:
        super().__init__()
        self.validate = Mock(side_effect=self._validate)
        self.update = Mock(side_effect=self._update)

    def _validate(self, table: str, values: list, *, rid: RID) -> list:
        if type(values[0]) is not int or type(values[1]) is not str:
            raise SchemaViolationError("Tipe nilai tidak sesuai skema")

        if any(row.rid != rid and row.values[0] == values[0] for row in self.rows):
            raise SchemaViolationError("Primary key sudah ada")

        return list(values)

    def _update(self, table: str, rid: RID, values: list, lsn: int | None) -> None:
        self.rows = [
            Row(row.rid, list(values)) if row.rid == rid else row
            for row in self.rows
        ]


class TestUpdate(unittest.TestCase):
    def setUp(self) -> None:
        self.sm = UpdateSM()
        self.ctx = FakeCtx(self.sm)
        self.new_name = Literal("baru", DataType.VARCHAR)

    def test_without_where_updates_all_rows(self) -> None:
        source = FakeSource()
        update = UpdateExecution("t", "name", self.new_name, source)

        result = update.execute(self.ctx)

        self.assertTrue(result.ok)
        self.assertEqual(result.affected_rows, 4)
        self.assertEqual(
            [row.values for row in self.sm.rows],
            [[i, "baru"] for i in range(4)],
        )
        self.assertEqual(ROWS[0].values, [0, "n0"])
        self.assertTrue(source.closed)

    def test_where_updates_only_matching_rows(self) -> None:
        condition = Comparison(
            CompOp.GE,
            ColumnRef("t", "id"),
            Literal(2, DataType.INTEGER),
        )
        source = FilterOp(FakeSource(), condition)
        update = UpdateExecution("t", "name", self.new_name, source)

        result = update.execute(self.ctx)

        self.assertEqual(result.affected_rows, 2)
        self.assertEqual(
            [row.values[1] for row in self.sm.rows],
            ["n0", "n1", "baru", "baru"],
        )

    def test_arithmetic_uses_old_value(self) -> None:
        expression = BinaryOp(
            ArithOp.ADD,
            ColumnRef("t", "id"),
            Literal(10, DataType.INTEGER),
        )
        update = UpdateExecution("t", "id", expression, FakeSource())

        result = update.execute(self.ctx)

        self.assertEqual(result.affected_rows, 4)
        self.assertEqual([row.values[0] for row in self.sm.rows], [10, 11, 12, 13])

    def test_validation_precedes_authorization_and_write(self) -> None:
        source = FakeSource([ROWS[0]])
        events = Mock()
        events.attach_mock(self.sm.validate, "validate")
        authorize = Mock(side_effect=self.ctx.authorize_rid)
        self.ctx.authorize_rid = authorize
        events.attach_mock(authorize, "authorize")
        events.attach_mock(self.sm.update, "update")

        self.sm.validate.return_value = [0, "BARU"]
        self.sm.validate.side_effect = None
        self.sm.update.side_effect = lambda *args: self.assertTrue(source.closed)
        update = UpdateExecution("t", "name", self.new_name, source)

        update.execute(self.ctx)

        self.assertEqual(
            [call[0] for call in events.mock_calls],
            ["validate", "authorize", "update"],
        )
        self.sm.validate.assert_called_once_with("t", [0, "baru"], rid=ROWS[0].rid)

        table, rid, action, change = authorize.call_args.args
        self.assertEqual((table, rid, action), ("t", ROWS[0].rid, Action.WRITE))
        self.assertEqual(
            (change.table, change.rid, change.op),
            (table, rid, ChangeOp.UPDATE),
        )
        self.assertEqual((change.before, change.after), ([0, "n0"], [0, "BARU"]))
        self.sm.update.assert_called_once_with("t", rid, [0, "BARU"], 7)

    def test_unchanged_primary_key_excludes_current_row(self) -> None:
        expression = ColumnRef("t", "id")
        update = UpdateExecution("t", "id", expression, FakeSource())

        result = update.execute(self.ctx)

        self.assertEqual(result.affected_rows, 4)
        self.assertEqual(self.sm.rows, ROWS)

    def test_duplicate_primary_key_never_reaches_authorization(self) -> None:
        source = FakeSource([ROWS[0]])
        duplicate_id = Literal(1, DataType.INTEGER)
        update = UpdateExecution("t", "id", duplicate_id, source)

        with self.assertRaises(SchemaViolationError):
            update.execute(self.ctx)

        self.assertEqual(self.ctx.calls, [])
        self.sm.update.assert_not_called()

    def test_empty_source_updates_nothing(self) -> None:
        source = FakeSource([])
        update = UpdateExecution("t", "name", self.new_name, source)

        result = update.execute(self.ctx)

        self.assertTrue(result.ok)
        self.assertEqual(result.affected_rows, 0)
        self.sm.validate.assert_not_called()
        self.sm.update.assert_not_called()
        self.assertEqual(self.ctx.calls, [])

    def test_denied_write_does_not_update(self) -> None:
        ctx = FakeCtx(self.sm, AccessStatus.ABORTED)
        update = UpdateExecution("t", "name", self.new_name, FakeSource())

        with self.assertRaises(TransactionAbortedError):
            update.execute(ctx)

        self.sm.update.assert_not_called()


if __name__ == "__main__":
    unittest.main()
