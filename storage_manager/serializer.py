
# SM-02: encode/decode tipe data (INTEGER, FLOAT, CHAR, VARCHAR)
# SM-03: serialisasi record (null bitmap + layout) menjadi bytes dan sebaliknya

from __future__ import annotations

import math
import struct
from common.catalog import ColumnSchema, DataType, TableSchema
from storage_manager.errors import SchemaViolationError
from storage_manager.types import Value

INTEGER_MIN = -(2 ** 63)
INTEGER_MAX = 2 ** 63 - 1


class RecordCodec:  # Encode/decode nilai dan record

    @staticmethod
    def encode_integer(value: int) -> bytes:
        # INTEGER -> 8 bytes big-endian signed.
        if isinstance(value, bool) or not isinstance(value, int):
            raise SchemaViolationError(f"INTEGER butuh nilai int, dapat {type(value).__name__}")
        if not (INTEGER_MIN <= value <= INTEGER_MAX):
            raise SchemaViolationError(f"INTEGER di luar rentang 64-bit: {value}")
        return struct.pack(">q", value)

    @staticmethod
    def decode_integer(data: bytes) -> int:
        # 8 bytes big-endian signed -> INTEGER.
        return struct.unpack(">q", data)[0]

    @staticmethod
    def encode_float(value: float) -> bytes:
        # FLOAT -> 8 bytes big-endian double.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SchemaViolationError(f"FLOAT butuh nilai numerik, dapat {type(value).__name__}")
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            raise SchemaViolationError(f"FLOAT tidak boleh NaN/infinity: {value}")
        return struct.pack(">d", f)

    @staticmethod
    def decode_float(data: bytes) -> float:
        # 8 bytes big-endian double -> FLOAT.
        return struct.unpack(">d", data)[0]

    @staticmethod
    def encode_char(value: str, length: int) -> bytes:
        # CHAR -> bytes UTF-8 (dipad spasi kanan sampai `length` byte).
        if not isinstance(value, str):
            raise SchemaViolationError(f"CHAR butuh nilai str, dapat {type(value).__name__}")
        encoded = value.encode("utf-8")
        if len(encoded) > length:
            raise SchemaViolationError(f"CHAR melebihi length {length}")
        return encoded.ljust(length, b" ")

    @staticmethod
    def decode_char(data: bytes) -> str:
        # bytes -> CHAR (spasi kanan dibuang).
        return data.decode("utf-8").rstrip(" ")

    @staticmethod
    def encode_varchar(value: str, length: int) -> bytes:
        # VARCHAR -> bytes (prefix panjang 2 byte + UTF-8, max length).
        if not isinstance(value, str):
            raise SchemaViolationError(f"VARCHAR butuh nilai str, dapat {type(value).__name__}")
        encoded = value.encode("utf-8")
        if len(encoded) > length:
            raise SchemaViolationError(f"VARCHAR value exceeds max length {length}")
        return struct.pack(">H", len(encoded)) + encoded

    @staticmethod
    def decode_varchar(data: bytes) -> str:
        # bytes -> VARCHAR (prefix panjang 2 byte + UTF-8).
        if len(data) < 2:
            return ""
        length = struct.unpack(">H", data[:2])[0]
        return data[2:2 + length].decode("utf-8")

    # Helpers untuk type codecs

    @staticmethod
    def _fixed_size(column: ColumnSchema) -> int:
        # Ukuran byte field panjang tetap (INTEGER/FLOAT/CHAR). VARCHAR variabel.
        match column.type:
            case DataType.INTEGER:
                return 8
            case DataType.FLOAT:
                return 8
            case DataType.CHAR:
                return column.length or 0
            case DataType.VARCHAR:
                raise ValueError("VARCHAR ukurannya variabel, bukan field panjang tetap")
        raise ValueError(f"Tipe tidak didukung: {column.type}")

    @staticmethod
    def _encode_value(column: ColumnSchema, value: Value) -> bytes:
        # Encode satu kolom panjang tetap (INTEGER/FLOAT/CHAR), value != None.
        match column.type:
            case DataType.INTEGER:
                return RecordCodec.encode_integer(value)  # type: ignore[arg-type]
            case DataType.FLOAT:
                return RecordCodec.encode_float(value)  # type: ignore[arg-type]
            case DataType.CHAR:
                return RecordCodec.encode_char(value, column.length or 0)  # type: ignore[arg-type]
        raise ValueError(f"Tipe tidak didukung: {column.type}")

    @staticmethod
    def _decode_value(column: ColumnSchema, data: bytes) -> Value:
        # Decode bytes field panjang tetap (INTEGER/FLOAT/CHAR).
        match column.type:
            case DataType.INTEGER:
                return RecordCodec.decode_integer(data)
            case DataType.FLOAT:
                return RecordCodec.decode_float(data)
            case DataType.CHAR:
                return RecordCodec.decode_char(data)
        raise ValueError(f"Tipe tidak didukung: {column.type}")

    # SM-03: serialisasi record

    @staticmethod
    def max_record_size(schema: TableSchema) -> int:
        size = (len(schema.columns) + 7) // 8
        for column in schema.columns:
            if column.type in (DataType.CHAR, DataType.VARCHAR):
                if type(column.length) is not int or column.length <= 0:
                    raise SchemaViolationError(f"Panjang kolom harus positif: {column.name}")
                if column.type == DataType.VARCHAR:
                    if column.length > 65535:
                        raise SchemaViolationError(
                            f"Panjang VARCHAR melebihi prefix 2 byte: {column.name}"
                        )
                    size += 2
                size += column.length
            elif column.type in (DataType.INTEGER, DataType.FLOAT):
                size += RecordCodec._fixed_size(column)
            else:
                raise SchemaViolationError(f"Tipe kolom tidak didukung: {column.name}")
        return size

    @staticmethod
    def record_size(schema: TableSchema, values: tuple[Value, ...]) -> int:
        # Ukuran aktual satu record (byte) untuk `values` ini.
        return len(RecordCodec.encode(schema, values))

    @staticmethod
    def validate(schema: TableSchema, values: tuple[Value, ...]) -> tuple[Value, ...]:
        # Memastikan nilai valid terhadap skema
        
        if len(values) != len(schema.columns):
            raise SchemaViolationError(
                f"Jumlah nilai ({len(values)}) != jumlah kolom ({len(schema.columns)})"
            )

        for col, val in zip(schema.columns, values):
            if val is None:
                if not col.nullable:
                    raise SchemaViolationError(f"Kolom {col.name} NOT NULL tapi nilai None")
                continue

            match col.type:
                case DataType.INTEGER:
                    if isinstance(val, bool) or not isinstance(val, int):
                        raise SchemaViolationError(
                            f"Kolom {col.name} (INTEGER) butuh int, dapat {type(val).__name__}"
                        )
                    if not (INTEGER_MIN <= val <= INTEGER_MAX):
                        raise SchemaViolationError(f"Kolom {col.name} INTEGER di luar rentang 64-bit")
                case DataType.FLOAT:
                    if isinstance(val, bool) or not isinstance(val, (int, float)):
                        raise SchemaViolationError(
                            f"Kolom {col.name} (FLOAT) butuh numerik, dapat {type(val).__name__}"
                        )
                    f = float(val)
                    if math.isnan(f) or math.isinf(f):
                        raise SchemaViolationError(f"Kolom {col.name} FLOAT tidak boleh NaN/infinity")
                case DataType.CHAR | DataType.VARCHAR:
                    if not isinstance(val, str):
                        raise SchemaViolationError(
                            f"Kolom {col.name} ({col.type.value}) butuh str, dapat {type(val).__name__}"
                        )
                    if col.length is not None and len(val.encode("utf-8")) > col.length:
                        raise SchemaViolationError(
                            f"Nilai kolom {col.name} melebihi length {col.length}"
                        )

        return values

    @staticmethod
    def encode(schema: TableSchema, values: tuple[Value, ...]) -> bytes:
        # Serialisasi satu record menjadi bytes (panjang variabel):
        
        RecordCodec.validate(schema, values)
        num_cols = len(schema.columns)
        null_bitmap_bytes = (num_cols + 7) // 8
        bitmap = bytearray(null_bitmap_bytes)
        fixed_parts = []
        varchar_parts = []

        for i, (col, val) in enumerate(zip(schema.columns, values)):
            if val is None:
                # Set bit ke 1 (null)
                bitmap[i // 8] |= (1 << (i % 8))
            elif col.type == DataType.VARCHAR:
                encoded = val.encode("utf-8")  # type: ignore[union-attr]
                varchar_parts.append(struct.pack(">H", len(encoded)) + encoded)
            else:
                fixed_parts.append(RecordCodec._encode_value(col, val))

        return bytes(bitmap) + b"".join(fixed_parts) + b"".join(varchar_parts)

    @staticmethod
    def decode(schema: TableSchema, data: bytes) -> tuple[Value, ...]:        
        # Deserialisasi bytes menjadi tuple nilai.
        
        num_cols = len(schema.columns)
        null_bitmap_bytes = (num_cols + 7) // 8

        if len(data) < null_bitmap_bytes:
            raise ValueError(
                f"Data terlalu pendek: butuh minimal {null_bitmap_bytes} byte untuk null bitmap"
            )

        bitmap = data[:null_bitmap_bytes]
        offset = null_bitmap_bytes
        values: list = [None] * num_cols

        # Pass 1: kolom fixed sesuai urutan schema (VARCHAR dan NULL dilewati).
        for i, col in enumerate(schema.columns):
            is_null = (bitmap[i // 8] >> (i % 8)) & 1
            if col.type == DataType.VARCHAR or is_null:
                continue
            col_size = RecordCodec._fixed_size(col)
            if offset + col_size > len(data):
                raise ValueError(f"Data terlalu pendek untuk kolom {col.name}")
            values[i] = RecordCodec._decode_value(col, data[offset:offset + col_size])
            offset += col_size

        # Pass 2: kolom VARCHAR sesuai urutan schema.
        for i, col in enumerate(schema.columns):
            is_null = (bitmap[i // 8] >> (i % 8)) & 1
            if col.type != DataType.VARCHAR or is_null:
                continue
            # Panjang variabel: 2 byte prefix + isi UTF-8
            if offset + 2 > len(data):
                raise ValueError(f"Data terlalu pendek untuk prefix VARCHAR {col.name}")
            actual_len = struct.unpack(">H", data[offset:offset + 2])[0]
            payload = data[offset + 2:offset + 2 + actual_len]
            if len(payload) < actual_len:
                raise ValueError(f"Data VARCHAR {col.name} terpotong")
            values[i] = payload.decode("utf-8")
            offset += 2 + actual_len

        return tuple(values)