from __future__ import annotations

import unittest
from unittest.mock import Mock

from common.errors import SchemaViolationError, StorageError, TableNotFoundError, TransactionAbortedError
from common.storage import RID
from common.transaction import NO_RECORD, AccessResponse, AccessStatus, ChangeOp
from query_processor.context import ExecContext
from query_processor.errors import ExecutionError
from query_processor.operators.insert import InsertExecution


class TestInsert(unittest.TestCase):
    def setUp(self) -> None:
        self.rid = RID("student", 2, 3)
        self.values = (1, "Budi", 3.5)
        self.calls = Mock()
        self.sm = self.calls.sm
        self.sm.validate.side_effect = lambda table, values: list(values)
        self.sm.allocate_rid.return_value = self.rid
        self.ctx = ExecContext(tx_id=9, sm=self.sm, ccm=Mock())
        self.ctx.authorize_insert = self.calls.authorize_insert
        self.calls.authorize_insert.return_value = AccessResponse(AccessStatus.ALLOWED, lsn=17)
        self.op = InsertExecution("student", self.values)

    def test_inserts_one_row_after_validation_and_authorization(self) -> None:
        normalized = [1, "Budi", 4.0]
        self.sm.validate.side_effect = None
        self.sm.validate.return_value = normalized
        result = self.op.execute(self.ctx)

        self.assertTrue(result.ok)
        self.assertEqual(result.affected_rows, 1)
        self.assertEqual(
            [call[0] for call in self.calls.mock_calls],
            ["sm.validate", "sm.allocate_rid", "authorize_insert", "sm.insert"],
        )
        self.sm.validate.assert_called_once_with("student", list(self.values))
        self.sm.allocate_rid.assert_called_once_with("student")
        self.sm.insert.assert_called_once_with("student", self.rid, normalized, 17)
        self.sm.release_rid.assert_not_called()
        change = self.calls.authorize_insert.call_args.args[0]
        self.assertEqual((change.table, change.rid, change.op), ("student", self.rid, ChangeOp.INSERT))
        self.assertIs(change.before, NO_RECORD)
        self.assertEqual(change.after, normalized)
        self.assertEqual(self.ctx.ccm.mock_calls, [])

    def test_validation_errors_propagate_before_allocation_or_logging(self) -> None:
        for error in (SchemaViolationError("invalid"), TableNotFoundError("missing")):
            with self.subTest(error=type(error).__name__):
                self.sm.validate.side_effect = error
                with self.assertRaises(type(error)) as raised:
                    self.op.execute(self.ctx)
                self.assertIs(raised.exception, error)
                self.sm.allocate_rid.assert_not_called()
                self.calls.authorize_insert.assert_not_called()
                self.sm.insert.assert_not_called()
                self.sm.release_rid.assert_not_called()

    def test_allocation_failure_does_not_authorize_or_release_unknown_rid(self) -> None:
        self.sm.allocate_rid.side_effect = StorageError("no free slot")
        with self.assertRaises(StorageError):
            self.op.execute(self.ctx)
        self.calls.authorize_insert.assert_not_called()
        self.sm.insert.assert_not_called()
        self.sm.release_rid.assert_not_called()

    def test_authorization_failure_releases_rid_without_writing(self) -> None:
        error = TransactionAbortedError("deadlock", 9)
        for mode in ("response", "exception"):
            with self.subTest(mode=mode):
                self.calls.reset_mock()
                self.calls.authorize_insert.return_value = AccessResponse(
                    AccessStatus.ABORTED, message="deadlock"
                )
                self.calls.authorize_insert.side_effect = error if mode == "exception" else None
                with self.assertRaises(TransactionAbortedError) as raised:
                    self.op.execute(self.ctx)
                self.assertEqual(raised.exception.tx_id, 9)
                self.assertEqual(raised.exception.message, "deadlock")
                if mode == "exception":
                    self.assertIs(raised.exception, error)
                self.sm.insert.assert_not_called()
                self.sm.release_rid.assert_called_once_with(self.rid)

    def test_incomplete_authorization_does_not_write(self) -> None:
        for status in (AccessStatus.WAIT, AccessStatus.ALLOWED):
            with self.subTest(status=status):
                self.calls.reset_mock()
                self.calls.authorize_insert.return_value = AccessResponse(status)
                with self.assertRaises(ExecutionError):
                    self.op.execute(self.ctx)
                self.sm.allocate_rid.assert_called_once_with("student")
                self.calls.authorize_insert.assert_called_once()
                self.sm.insert.assert_not_called()
                self.sm.release_rid.assert_called_once_with(self.rid)

    def test_storage_error_propagates_for_transaction_rollback_and_releases_rid(self) -> None:
        error = StorageError("write failed")
        self.sm.insert.side_effect = error
        with self.assertRaises(StorageError) as raised:
            self.op.execute(self.ctx)
        self.assertIs(raised.exception, error)
        self.sm.release_rid.assert_called_once_with(self.rid)
        self.assertEqual(self.ctx.ccm.mock_calls, [])


if __name__ == "__main__":
    unittest.main()
