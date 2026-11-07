"""Uji RecordCodec (SM-02 + SM-03)."""

from __future__ import annotations

import unittest

from common.catalog import ColumnSchema, DataType, TableSchema
from storage_manager.errors import SchemaViolationError
from storage_manager.serializer import RecordCodec
from storage_manager.types import Value


class TestRecordCodecSM02(unittest.TestCase):
    """Test SM-02: codec per-tipe."""

    def test_encode_decode_integer(self):
        for val in [0, 1, -1, 42, -42, 2 ** 63 - 1, -(2 ** 63)]:
            encoded = RecordCodec.encode_integer(val)
            self.assertEqual(len(encoded), 8)
            decoded = RecordCodec.decode_integer(encoded)
            self.assertEqual(decoded, val)

    def test_encode_decode_float(self):
        for val in [0.0, 1.5, -1.5, 3.14159, -2.71828, 1e10, -1e10]:
            encoded = RecordCodec.encode_float(val)
            self.assertEqual(len(encoded), 8)
            decoded = RecordCodec.decode_float(encoded)
            self.assertAlmostEqual(decoded, val)

    def test_encode_integer_rejects_bool_and_out_of_range(self):
        with self.assertRaises(SchemaViolationError):
            RecordCodec.encode_integer(True)
        with self.assertRaises(SchemaViolationError):
            RecordCodec.encode_integer(2 ** 63)
        with self.assertRaises(SchemaViolationError):
            RecordCodec.encode_integer(-(2 ** 63) - 1)

    def test_encode_float_rejects_nan_and_inf(self):
        with self.assertRaises(SchemaViolationError):
            RecordCodec.encode_float(float("nan"))
        with self.assertRaises(SchemaViolationError):
            RecordCodec.encode_float(float("inf"))

    def test_encode_decode_char(self):
        # CHAR(5) pad with spaces
        encoded = RecordCodec.encode_char("hi", 5)
        self.assertEqual(encoded, b"hi   ")
        decoded = RecordCodec.decode_char(encoded)
        self.assertEqual(decoded, "hi")

        # Exact length
        encoded = RecordCodec.encode_char("abcde", 5)
        self.assertEqual(encoded, b"abcde")

    def test_encode_char_too_long_rejected(self):
        with self.assertRaises(SchemaViolationError):
            RecordCodec.encode_char("hello world", 5)

    def test_encode_char_multibyte_measured_in_bytes(self):
        # "héllo" = 5 karakter tetapi 6 byte UTF-8 -> ditolak CHAR(5)
        with self.assertRaises(SchemaViolationError):
            RecordCodec.encode_char("héllo", 5)

    def test_encode_decode_varchar(self):
        # VARCHAR(10) with length prefix
        encoded = RecordCodec.encode_varchar("hello", 10)
        self.assertEqual(encoded[:2], b"\x00\x05")  # length = 5
        self.assertEqual(encoded[2:], b"hello")
        decoded = RecordCodec.decode_varchar(encoded)
        self.assertEqual(decoded, "hello")

        # Empty string
        encoded = RecordCodec.encode_varchar("", 10)
        self.assertEqual(encoded[:2], b"\x00\x00")
        decoded = RecordCodec.decode_varchar(encoded)
        self.assertEqual(decoded, "")

    def test_encode_varchar_too_long_rejected(self):
        with self.assertRaises(SchemaViolationError):
            RecordCodec.encode_varchar("hello world", 5)

    def test_fixed_size(self):
        self.assertEqual(RecordCodec._fixed_size(ColumnSchema("i", DataType.INTEGER)), 8)
        self.assertEqual(RecordCodec._fixed_size(ColumnSchema("f", DataType.FLOAT)), 8)
        self.assertEqual(RecordCodec._fixed_size(ColumnSchema("c", DataType.CHAR, length=10)), 10)
        with self.assertRaises(ValueError):
            RecordCodec._fixed_size(ColumnSchema("v", DataType.VARCHAR, length=20))


class TestRecordCodecSM03(unittest.TestCase):
    """Test SM-03: serialisasi record."""

    def setUp(self):
        self.schema = TableSchema(
            "test",
            columns=[
                ColumnSchema("id", DataType.INTEGER, nullable=False),
                ColumnSchema("name", DataType.CHAR, length=10, nullable=True),
                ColumnSchema("score", DataType.FLOAT, nullable=True),
                ColumnSchema("tag", DataType.VARCHAR, length=20, nullable=True),
            ],
        )

    def test_record_size(self):
        # 4 kolom: 1 byte bitmap + id(8) + name CHAR(10) + score(8) + tag VARCHAR "tag123"(2+6) = 35
        values: tuple[Value, ...] = (42, "hello", 2.5, "tag123")
        self.assertEqual(RecordCodec.record_size(self.schema, values), 35)

    def test_record_size_varies_with_varchar_length(self):
        small = RecordCodec.record_size(self.schema, (1, "abc", 3.14, "a"))
        long = RecordCodec.record_size(self.schema, (1, "abc", 3.14, "a" * 20))
        self.assertEqual(long - small, 19)

    def test_encode_decode_two_varchars(self):
        schema = TableSchema(
            "t",
            columns=[
                ColumnSchema("id", DataType.INTEGER, nullable=False),
                ColumnSchema("a", DataType.VARCHAR, length=10),
                ColumnSchema("b", DataType.VARCHAR, length=10),
            ],
        )
        values: tuple[Value, ...] = (1, "x", "yyyyy")
        encoded = RecordCodec.encode(schema, values)
        self.assertEqual(RecordCodec.decode(schema, encoded), values)
        # 1 byte bitmap + 8 (id) + (2+1) + (2+5) = 19
        self.assertEqual(len(encoded), 19)

    def test_validate_ok(self):
        values: tuple[Value, ...] = (1, "abc", 3.14, "tag")
        result = RecordCodec.validate(self.schema, values)
        self.assertEqual(result, values)

    def test_validate_wrong_count(self):
        with self.assertRaises(SchemaViolationError):
            RecordCodec.validate(self.schema, (1, "abc"))

    def test_validate_not_null_violation(self):
        with self.assertRaises(SchemaViolationError):
            RecordCodec.validate(self.schema, (None, "abc", 3.14, "tag"))

    def test_validate_char_too_long(self):
        with self.assertRaises(SchemaViolationError):
            RecordCodec.validate(self.schema, (1, "x" * 11, 3.14, "tag"))

    def test_validate_varchar_too_long(self):
        with self.assertRaises(SchemaViolationError):
            RecordCodec.validate(self.schema, (1, "abc", 3.14, "x" * 21))

    def test_validate_type_mismatch(self):
        # Kolom INTEGER diberi string
        with self.assertRaises(SchemaViolationError):
            RecordCodec.validate(self.schema, ("bukan-int", "abc", 3.14, "tag"))

    def test_validate_bool_rejected(self):
        with self.assertRaises(SchemaViolationError):
            RecordCodec.validate(self.schema, (True, "abc", 3.14, "tag"))

    def test_validate_integer_out_of_range(self):
        with self.assertRaises(SchemaViolationError):
            RecordCodec.validate(self.schema, (2 ** 63, "abc", 3.14, "tag"))

    def test_validate_float_nan_rejected(self):
        with self.assertRaises(SchemaViolationError):
            RecordCodec.validate(self.schema, (1, "abc", float("nan"), "tag"))

    def test_encode_decode_full(self):
        values: tuple[Value, ...] = (42, "hello", 2.5, "tag123")
        encoded = RecordCodec.encode(self.schema, values)
        # 1 bitmap + id(8) + name(10) + score(8) + tag(2+6) = 35
        self.assertEqual(len(encoded), 35)
        decoded = RecordCodec.decode(self.schema, encoded)
        self.assertEqual(decoded, values)

    def test_encode_decode_with_nulls(self):
        values: tuple[Value, ...] = (1, None, 3.14, None)
        encoded = RecordCodec.encode(self.schema, values)
        decoded = RecordCodec.decode(self.schema, encoded)
        self.assertEqual(decoded, values)

    def test_encode_decode_all_nulls(self):
        # all nullable columns null
        values: tuple[Value, ...] = (1, None, None, None)
        encoded = RecordCodec.encode(self.schema, values)
        decoded = RecordCodec.decode(self.schema, encoded)
        self.assertEqual(decoded, values)

    def test_decode_empty_data_rejected(self):
        with self.assertRaises(ValueError):
            RecordCodec.decode(self.schema, b"")

    def test_roundtrip_multiple(self):
        test_cases = [
            (1, "a", 1.0, "x"),
            (2, "bb", 2.0, "yy"),
            (3, "ccc", 3.0, "zzz"),
            (4, None, None, None),
            (5, "test", None, "varchar"),
        ]
        for values in test_cases:
            with self.subTest(values=values):
                encoded = RecordCodec.encode(self.schema, values)
                decoded = RecordCodec.decode(self.schema, encoded)
                self.assertEqual(decoded, values)


class TestRecordCodecStub(unittest.TestCase):
    """Kompatibilitas: method harus ada (stub check)."""

    def test_methods_exist(self):
        for name in [
            "encode_integer",
            "decode_integer",
            "encode_float",
            "decode_float",
            "encode_char",
            "decode_char",
            "encode_varchar",
            "decode_varchar",
            "record_size",
            "validate",
            "encode",
            "decode",
        ]:
            self.assertTrue(callable(getattr(RecordCodec, name)), name)


if __name__ == "__main__":
    unittest.main()