"""Uji serialisasi dan parsing catatan log (FR-01)."""

from __future__ import annotations

import json
import unittest

from common.log import LogKind, LogRecord
from common.storage import RID
from common.transaction import NO_RECORD, ChangeOp, DataChange
from failure_recovery.log import codec


def _data_record() -> LogRecord:
    return LogRecord(
        kind=LogKind.DATA,
        tx_id=7,
        lsn=3,
        timestamp=1730000000.5,
        active_tx=[7, 8],
        change=DataChange(
            table="student",
            rid=RID("student", 0, 2),
            op=ChangeOp.UPDATE,
            before=[1, "Budi"],
            after=[2, "Budi", 3.5],
        ),
    )


class TestSerialize(unittest.TestCase):
    def test_akhiran_newline_satu_baris(self):
        data = codec.serialize(_data_record())
        self.assertIsInstance(data, bytes)
        self.assertTrue(data.endswith(b"\n"))
        self.assertEqual(len(data.splitlines()), 1)

    def test_isi_payload(self):
        payload = json.loads(codec.serialize(_data_record()))
        self.assertEqual(payload["kind"], "DATA")
        self.assertEqual(payload["lsn"], 3)
        self.assertEqual(payload["tx_id"], 7)
        self.assertEqual(payload["change"]["rid"], ["student", 0, 2])
        self.assertEqual(payload["change"]["op"], "UPDATE")

    def test_no_record_jadi_null(self):
        record = _data_record()
        record.change.before = NO_RECORD
        payload = json.loads(codec.serialize(record))
        self.assertIsNone(payload["change"]["before"])

    def test_record_tanpa_change(self):
        payload = json.loads(codec.serialize(LogRecord(LogKind.BEGIN, tx_id=1)))
        self.assertIsNone(payload["change"])

    def test_record_tidak_valid_kembali_none(self):
        self.assertIsNone(codec.serialize("bukan record"))
        record = _data_record()
        record.kind = "DATA"
        self.assertIsNone(codec.serialize(record))


class TestParse(unittest.TestCase):
    def test_round_trip_data(self):
        record = _data_record()
        parsed = codec.parse(codec.serialize(record))
        self.assertEqual(parsed, record)
        self.assertIsInstance(parsed.change.before, list)

    def test_round_trip_semua_log_kind(self):
        for kind in LogKind:
            with self.subTest(kind=kind):
                record = LogRecord(kind, tx_id=2, lsn=9, timestamp=0.5)
                self.assertEqual(codec.parse(codec.serialize(record)), record)

    def test_terima_bytes_dan_str(self):
        record = _data_record()
        self.assertEqual(codec.parse(codec.serialize(record)), record)
        self.assertEqual(codec.parse(codec.serialize(record).decode()), record)

    def test_no_record_dari_null(self):
        line = json.dumps(
            {
                "kind": "DATA",
                "lsn": 1,
                "timestamp": 0.0,
                "tx_id": 1,
                "active_tx": [],
                "change": {
                    "table": "t",
                    "rid": ["t", 0, 0],
                    "op": "INSERT",
                    "before": None,
                    "after": [1],
                },
            }
        )
        parsed = codec.parse(line)
        self.assertIs(parsed.change.before, NO_RECORD)
        self.assertEqual(parsed.change.after, [1])

    def test_baris_rusak_kembali_none(self):
        for bad in (b"", "", "   ", "{rusak", b"\xff\xfe", '"string"', "[]", '{"kind":"PAKSA"}'):
            with self.subTest(bad=bad):
                self.assertIsNone(codec.parse(bad))

    def test_field_wajib_hilang_kembali_none(self):
        self.assertIsNone(codec.parse('{"kind":"BEGIN","tx_id":1}'))


class TestPath(unittest.TestCase):
    def test_set_get_log_path(self):
        original = codec.get_log_path()
        self.addCleanup(codec.set_log_path, original)
        codec.set_log_path("/tmp/wal-test.log")
        self.assertEqual(codec.get_log_path().name, "wal-test.log")


if __name__ == "__main__":
    unittest.main()
