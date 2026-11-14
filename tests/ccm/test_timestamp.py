import unittest

from common.storage import RID
from common.transaction import AccessStatus, Action, ResourceId
from concurrency_control.protocol import Transaction
from concurrency_control.timestamp import TimestampProtocol

ROW = ResourceId("A", RID("A", 0, 0))


class StrictTimestampTest(unittest.TestCase):
    def setUp(self) -> None:
        self.ts = TimestampProtocol()
        self.a, self.b = Transaction(1, 1), Transaction(2, 2)

    def test_older_reader_after_newer_write_aborts(self) -> None:
        self.ts.request(self.b, ROW, Action.WRITE)
        self.assertIs(self.ts.request(self.a, ROW, Action.READ).status, AccessStatus.ABORTED)

    def test_uncommitted_write_blocks_other_writer_and_reader(self) -> None:
        self.assertIs(self.ts.request(self.a, ROW, Action.WRITE).status, AccessStatus.ALLOWED)
        self.assertIs(self.ts.request(self.b, ROW, Action.WRITE).status, AccessStatus.WAIT)
        self.assertIs(self.ts.request(self.b, ROW, Action.READ).status, AccessStatus.WAIT)
        self.assertIs(self.ts.request(self.a, ROW, Action.WRITE).status, AccessStatus.ALLOWED)
        self.ts.on_finish(self.a.tx_id)
        self.assertIs(self.ts.request(self.b, ROW, Action.WRITE).status, AccessStatus.ALLOWED)


if __name__ == "__main__":
    unittest.main()
