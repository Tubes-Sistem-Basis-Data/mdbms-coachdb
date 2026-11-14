from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from common import config
from common.errors import MdbmsError, TransactionAbortedError
from common.interfaces import ConcurrencyControlManager, StorageManager
from common.storage import RID
from common.transaction import (
    NO_RECORD, AccessResponse, AccessStatus, Action, ChangeOp, DataChange, ResourceId,
)
from query_processor.context import ExecContext
from query_processor.errors import ExecutionError
from query_processor.operators.insert import InsertExecution


class TestExecContext(unittest.TestCase):
    def setUp(self) -> None:
        self.sm = Mock(spec=StorageManager)
        self.ccm = Mock(spec=ConcurrencyControlManager)
        self.ctx = ExecContext(7, self.sm, self.ccm)
        self.rid = RID("student", 0, 1)
        self.change = DataChange("student", self.rid, ChangeOp.UPDATE, [1, "old"], [1, "new"])
        self.allowed = AccessResponse(AccessStatus.ALLOWED, lsn=12)
        self.ccm.validate_object.return_value = self.allowed

    def test_table_scan_requests_read_even_with_write_intent(self) -> None:
        self.ctx.access_intent = Action.WRITE

        response = self.ctx.authorize_table("student")

        self.assertIs(response, self.allowed)
        self.ccm.validate_object.assert_called_once_with(
            7, ResourceId("student"), Action.READ, None
        )

    def test_row_read_does_not_send_a_data_change(self) -> None:
        self.ccm.validate_object.return_value = AccessResponse(AccessStatus.ALLOWED)

        response = self.ctx.authorize_rid("student", self.rid, Action.READ)

        self.assertIsNone(response.lsn)
        self.ccm.validate_object.assert_called_once_with(
            7, ResourceId("student", self.rid), Action.READ, None
        )

    def test_write_forwards_before_after_images_and_returns_lsn(self) -> None:
        response = self.ctx.authorize_rid("student", self.rid, Action.WRITE, self.change)

        self.assertIs(response, self.allowed)
        self.assertIs(self.ccm.validate_object.call_args.args[3], self.change)
        self.ccm.validate_object.assert_called_once_with(
            7, ResourceId("student", self.rid), Action.WRITE, self.change
        )
        self.sm.assert_not_called()

    def test_insert_authorizes_the_reserved_rid(self) -> None:
        change = DataChange("student", self.rid, ChangeOp.INSERT, NO_RECORD, [1, "new"])

        self.assertIs(self.ctx.authorize_insert(change), self.allowed)

        self.ccm.validate_object.assert_called_once_with(
            7, ResourceId("student", self.rid), Action.WRITE, change
        )

    @patch("query_processor.context.sleep")
    def test_wait_retries_the_same_request_until_allowed(self, sleep: Mock) -> None:
        self.ccm.validate_object.side_effect = [
            AccessResponse(AccessStatus.WAIT), AccessResponse(AccessStatus.WAIT), self.allowed,
        ]

        response = self.ctx.authorize_rid("student", self.rid, Action.WRITE, self.change)

        self.assertIs(response, self.allowed)
        self.assertEqual(sleep.call_count, 2)
        sleep.assert_called_with(config.WAIT_RETRY_SECONDS)
        calls = self.ccm.validate_object.call_args_list
        self.assertEqual(len(calls), 3)
        for call in calls:
            self.assertIs(call.args[1], calls[0].args[1])
            self.assertIs(call.args[3], self.change)
        self.ccm.abort.assert_not_called()
        self.ccm.commit.assert_not_called()

    @patch("query_processor.context.sleep")
    def test_wait_then_aborted_stops_without_a_second_abort(self, sleep: Mock) -> None:
        self.ccm.validate_object.side_effect = [
            AccessResponse(AccessStatus.WAIT),
            AccessResponse(AccessStatus.ABORTED, message="deadlock"),
        ]

        with self.assertRaises(TransactionAbortedError) as raised:
            self.ctx.authorize_table("student")

        self.assertEqual((raised.exception.tx_id, raised.exception.message), (7, "deadlock"))
        self.assertEqual(self.ccm.validate_object.call_count, 2)
        sleep.assert_called_once_with(config.WAIT_RETRY_SECONDS)
        self.ccm.abort.assert_not_called()

    def test_invalid_changes_are_rejected_before_logging(self) -> None:
        other = RID("student", 0, 2)
        cases = [
            None,
            DataChange("other", self.rid, ChangeOp.UPDATE, [1], [2]),
            DataChange("student", other, ChangeOp.UPDATE, [1], [2]),
            DataChange("student", self.rid, ChangeOp.INSERT, None, [1]),
            DataChange("student", self.rid, ChangeOp.UPDATE, NO_RECORD, [1]),
            DataChange("student", self.rid, ChangeOp.DELETE, [1], None),
        ]
        for change in cases:
            with self.subTest(change=change), self.assertRaises(ExecutionError):
                self.ctx.authorize_rid("student", self.rid, Action.WRITE, change)
        self.ccm.validate_object.assert_not_called()

    def test_read_cannot_log_a_write_or_use_another_tables_rid(self) -> None:
        with self.assertRaises(ExecutionError):
            self.ctx.authorize_rid("student", self.rid, Action.READ, self.change)
        with self.assertRaises(ExecutionError):
            self.ctx.authorize_rid("other", self.rid, Action.READ)
        with self.assertRaises(ExecutionError):
            self.ctx.authorize_insert(self.change)
        self.ccm.validate_object.assert_not_called()

    def test_access_requires_a_transaction(self) -> None:
        self.ctx.tx_id = None
        with self.assertRaises(ExecutionError):
            self.ctx.authorize_table("student")
        self.ccm.validate_object.assert_not_called()

    def test_invalid_response_or_write_lsn_never_allows_storage_write(self) -> None:
        self.sm.validate.side_effect = lambda table, values: values
        self.sm.allocate_rid.return_value = self.rid
        for response in (
            None, AccessResponse("ALLOWED"),
            *(AccessResponse(AccessStatus.ALLOWED, lsn=lsn) for lsn in (None, -1, 0, True, "12")),
        ):
            with self.subTest(response=response):
                self.sm.reset_mock()
                self.ccm.validate_object.return_value = response
                with self.assertRaises(ExecutionError):
                    InsertExecution("student", (1, "new")).execute(self.ctx)
                self.sm.insert.assert_not_called()
                self.sm.release_rid.assert_called_once_with(self.rid)

    def test_rollback_failure_propagates_without_claiming_aborted(self) -> None:
        error = MdbmsError("Rollback belum selesai")
        self.ccm.validate_object.side_effect = error

        with self.assertRaises(MdbmsError) as raised:
            self.ctx.authorize_rid("student", self.rid, Action.WRITE, self.change)

        self.assertIs(raised.exception, error)
        self.assertNotIsInstance(raised.exception, TransactionAbortedError)
        self.ccm.abort.assert_not_called()


if __name__ == "__main__":
    unittest.main()
