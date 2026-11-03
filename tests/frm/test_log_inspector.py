"""Uji penampil isi log untuk driver dan debugging (FR-03)."""

from __future__ import annotations

import json
import unittest

from common.log import LogKind, LogRecord
from common.storage import RID
from common.transaction import NO_RECORD, ChangeOp, DataChange
from failure_recovery.log import codec
from failure_recovery.log.log_inspector import LogInspector


def _data_record(tx_id: int, lsn: int) -> LogRecord:
    return LogRecord(
        kind=LogKind.DATA,
        tx_id=tx_id,
        lsn=lsn,
        timestamp=float(lsn),
        change=DataChange(
            table="student",
            rid=RID("student", 0, lsn),
            op=ChangeOp.INSERT,
            before=NO_RECORD,
            after=[lsn, "Budi"],
        ),
    )


class DisplayTestCase(unittest.TestCase):
    def setUp(self):
        self.inspector = LogInspector()

    def _baris(self, records: list[LogRecord]) -> list[str]:
        output = self.inspector.display(records)
        return output.splitlines() if output else []


class TestDisplay(DisplayTestCase):
    def test_json_per_baris_untuk_semua_log_kind(self):
        records = [
            LogRecord(LogKind.BEGIN, tx_id=1, lsn=1, timestamp=1.0),
            _data_record(1, 2),
            LogRecord(LogKind.COMMIT, tx_id=1, lsn=3, timestamp=3.0),
            LogRecord(LogKind.ABORT_REQUEST, tx_id=2, lsn=4, timestamp=4.0),
            LogRecord(LogKind.ABORT, tx_id=2, lsn=5, timestamp=5.0),
            LogRecord(LogKind.CHECKPOINT_BEGIN, lsn=6, timestamp=6.0),
            LogRecord(LogKind.CHECKPOINT_END, lsn=7, timestamp=7.0),
        ]
        baris = self._baris(records)
        self.assertEqual(len(baris), len(records))
        for baris_teks, record in zip(baris, records):
            payload = json.loads(baris_teks)
            self.assertEqual(payload["kind"], record.kind.value)
            self.assertEqual(payload["lsn"], record.lsn)

    def test_setiap_baris_round_trip_ke_record_asli(self):
        records = [
            LogRecord(LogKind.BEGIN, tx_id=9, lsn=1, timestamp=1.5),
            _data_record(9, 2),
        ]
        for baris_teks, asli in zip(self._baris(records), records):
            self.assertEqual(codec.parse(baris_teks), asli)

    def test_urutan_lsn_dipertahankan(self):
        records = [
            LogRecord(LogKind.BEGIN, tx_id=1, lsn=1),
            LogRecord(LogKind.COMMIT, tx_id=2, lsn=2),
            LogRecord(LogKind.BEGIN, tx_id=3, lsn=3),
        ]
        lsns = [json.loads(b)["lsn"] for b in self._baris(records)]
        self.assertEqual(lsns, [1, 2, 3])

    def test_daftar_kosong_hasilnya_kosong(self):
        self.assertEqual(self.inspector.display([]), "")

    def test_record_tidak_valid_dilewati(self):
        baris = self._baris([LogRecord(LogKind.BEGIN, tx_id=1, lsn=1), "rusak"])
        self.assertEqual(len(baris), 1)


if __name__ == "__main__":
    unittest.main()
