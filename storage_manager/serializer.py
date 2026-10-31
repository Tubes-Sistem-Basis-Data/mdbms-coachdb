"""
RecordCodec menggabungkan dua fitur:
- SM-02: encode/decode tipe data (INTEGER, FLOAT, CHAR, VARCHAR)
- SM-03: serialisasi record (null bitmap + layout) menjadi bytes dan sebaliknya
"""

from __future__ import annotations

from common.catalog import ColumnSchema, TableSchema
from storage_manager.types import Value


class RecordCodec: # Encode/decode nilai dan record

    # SM-02: codec per-tipe

    @staticmethod
    def encode_integer(value: int) -> bytes:
        # INTEGER -> bytes
        return b""

    @staticmethod
    def decode_integer(data: bytes) -> int:
        # bytes -> INTEGER
        return 0

    @staticmethod
    def encode_float(value: float) -> bytes:
        # FLOAT -> bytes
        return b""

    @staticmethod
    def decode_float(data: bytes) -> float:
        # bytes -> FLOAT
        return 0.0

    @staticmethod
    def encode_char(value: str, length: int) -> bytes:
        # CHAR -> bytes (dipad spasi)
        return b""

    @staticmethod
    def decode_char(data: bytes) -> str:
        # bytes -> CHAR (spasi kanan dibuang)
        return ""

    @staticmethod
    def encode_varchar(value: str, length: int) -> bytes:
        # VARCHAR -> bytes (prefix panjang)
        return b""

    @staticmethod
    def decode_varchar(data: bytes) -> str:
        # bytes -> VARCHAR
        return ""

    # SM-03: serialisasi record

    @staticmethod
    def record_size(columns: tuple[ColumnSchema, ...]) -> int:
        # Ukuran tetap satu record (byte)
        return 0

    @staticmethod
    def validate(schema: TableSchema, values: tuple[Value, ...]) -> tuple[Value, ...]:
        # Memastikan nilai valid terhadap skema
        return ()

    @staticmethod
    def encode(schema: TableSchema, values: tuple[Value, ...]) -> bytes:
        # Serialisasi satu record menjadi bytes
        return b""

    @staticmethod
    def decode(schema: TableSchema, data: bytes) -> tuple[Value, ...]:
        # Deserialisasi bytes menjadi tuple nilai
        return ()
