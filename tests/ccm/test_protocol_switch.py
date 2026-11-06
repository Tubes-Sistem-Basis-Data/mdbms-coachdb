import unittest

from common.errors import MdbmsError
from common.transaction import ProtocolType
from concurrency_control.lock_manager import LockManager
from concurrency_control.manager import CCManager, ProtocolSwitchError
from concurrency_control.timestamp import TimestampProtocol


class _Bridge:
    def log_begin(self, tx_id: int) -> int:
        return tx_id

    def log_commit(self, tx_id: int) -> int:
        return tx_id


class ProtocolSwitchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.ccm = CCManager(frm=None)
        self.ccm._bridge = _Bridge()

    def test_default_lock_based(self) -> None:
        self.assertIs(self.ccm.get_protocol(), ProtocolType.LOCK_BASED)
        self.assertIsInstance(self.ccm._protocol, LockManager)

    def test_switch_when_idle(self) -> None:
        self.ccm.set_protocol(ProtocolType.TIMESTAMP_BASED)
        self.assertIs(self.ccm.get_protocol(), ProtocolType.TIMESTAMP_BASED)
        self.assertIsInstance(self.ccm._protocol, TimestampProtocol)

        self.ccm.set_protocol(ProtocolType.LOCK_BASED)
        self.assertIs(self.ccm.get_protocol(), ProtocolType.LOCK_BASED)
        self.assertIsInstance(self.ccm._protocol, LockManager)

    def test_same_protocol_keeps_instance(self) -> None:
        before = self.ccm._protocol
        self.ccm.set_protocol(ProtocolType.LOCK_BASED)
        self.assertIs(self.ccm._protocol, before)

    def test_switch_with_active_tx_raises(self) -> None:
        tx_id = self.ccm.begin_transaction()
        with self.assertRaises(ProtocolSwitchError) as caught:
            self.ccm.set_protocol(ProtocolType.TIMESTAMP_BASED)
        self.assertIsInstance(caught.exception, MdbmsError)
        self.assertIs(self.ccm.get_protocol(), ProtocolType.LOCK_BASED)

        self.ccm.commit(tx_id)
        self.ccm.set_protocol(ProtocolType.TIMESTAMP_BASED)
        self.assertIs(self.ccm.get_protocol(), ProtocolType.TIMESTAMP_BASED)


if __name__ == "__main__":
    unittest.main()
