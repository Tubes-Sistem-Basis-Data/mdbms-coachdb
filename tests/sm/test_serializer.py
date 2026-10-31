"""Uji stub RecordCodec (SM-02 + SM-03)."""

from __future__ import annotations

import unittest

from common.catalog import TableSchema
from storage_manager.serializer import RecordCodec

_SCHEMA = TableSchema("t", [])

# nama method -> (args,)
_METHODS = {
    # SM-02: codec per-tipe
    "encode_integer": (0,),
    "decode_integer": (b"",),
    "encode_float": (0.0,),
    "decode_float": (b"",),
    "encode_char": ("a", 1),
    "decode_char": (b"",),
    "encode_varchar": ("a", 1),
    "decode_varchar": (b"",),
    # SM-03: serialisasi record
    "record_size": ((),),
    "validate": (_SCHEMA, ()),
    "encode": (_SCHEMA, ()),
    "decode": (_SCHEMA, b""),
}


class TestRecordCodecStub(unittest.TestCase):
    def test_methods_exist(self):
        for name in _METHODS:
            self.assertTrue(callable(getattr(RecordCodec, name)), name)

    def test_methods_raise_not_implemented(self):
        for name, args in _METHODS.items():
            with self.subTest(method=name):
                with self.assertRaises(NotImplementedError):
                    getattr(RecordCodec, name)(*args)


if __name__ == "__main__":
    unittest.main()
