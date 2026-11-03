"""Uji pembaca Write-Ahead Log (FR-01)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from common.log import LogKind, LogRecord
from common.storage import RID
from common.transaction import ChangeOp, DataChange
from failure_recovery.log import codec
from failure_recovery.log.log_reader import LogReader


def _data(tx_id: int, lsn: int, table: str = "student") -> LogRecord:
    return LogRecord(
        kind=LogKind.DATA,
        tx_id=tx_id,
        lsn=lsn,
        timestamp=float(lsn),
        change=DataChange(
            table=table,
            rid=RID(table, 0, lsn),
            op=ChangeOp.UPDATE,
            before=[lsn],
            after=[lsn + 1],
        ),
    )


class LogReaderTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "wal.log"
        self.reader = LogReader(str(self.path))

    def _tulis(self, *records: LogRecord, baris_rusak: bytes = b"") -> None:
        with open(self.path, "wb") as handle:
            for record in records:
                handle.write(codec.serialize(record))
            if baris_rusak:
                handle.write(baris_rusak)

    def test_file_tidak_ada_kembali_kosong(self):
        self.assertEqual(self.reader.read_log(-1), [])

    def test_read_log_filter_transaksi(self):
        self._tulis(
            _data(1, 1),
            _data(1, 2),
            _data(2, 3, table="course"),
            LogRecord(LogKind.COMMIT, tx_id=1, lsn=4, timestamp=4.0),
        )
        self.assertEqual(len(self.reader.read_log(-1)), 4)
        tx1 = self.reader.read_log(1)
        self.assertEqual([r.lsn for r in tx1], [1, 2, 4])
        self.assertEqual([r.lsn for r in self.reader.read_log(2)], [3])
        self.assertEqual(self.reader.read_log(99), [])

    def test_forward_scan_dari_lsn(self):
        self._tulis(_data(1, 1), _data(1, 5), _data(1, 9))
        self.assertEqual([r.lsn for r in self.reader.forward_scan(5)], [5, 9])
        self.assertEqual([r.lsn for r in self.reader.forward_scan(99)], [])

    def test_backward_scan_transaksi_urut_menurun(self):
        self._tulis(_data(1, 1), _data(2, 2), _data(1, 3), _data(1, 7))
        self.assertEqual([r.lsn for r in self.reader.backward_scan(1)], [7, 3, 1])
        self.assertEqual([r.lsn for r in self.reader.backward_scan(2)], [2])

    def test_baris_rusak_terakhir_dilewati(self):
        self._tulis(_data(1, 1), baris_rusak=b'{"kind":"DATA","lsn":2,')
        self.assertEqual([r.lsn for r in self.reader.read_log(-1)], [1])

    def test_baris_kosong_dilewati(self):
        self._tulis(_data(1, 1))
        with open(self.path, "ab") as handle:
            handle.write(b"\n\n")
        self.assertEqual([r.lsn for r in self.reader.read_log(-1)], [1])

    def test_path_default_mengikuti_codec(self):
        original = codec.get_log_path()
        self.addCleanup(codec.set_log_path, original)
        self._tulis(_data(3, 1))
        codec.set_log_path(self.path)
        reader = LogReader()
        self.assertEqual([r.lsn for r in reader.read_log(-1)], [1])


if __name__ == "__main__":
    unittest.main()
