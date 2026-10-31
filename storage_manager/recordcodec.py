"""
RecordCodec menggabungkan dua fitur:
- SM-02: encode/decode tipe data (INTEGER, FLOAT, CHAR, VARCHAR)
- SM-03: serialisasi record (null bitmap + layout) menjadi bytes dan sebaliknya
"""

from __future__ import annotations

from common.catalog import ColumnSchema, TableSchema


class RecordCodec: # Encode/decode nilai dan record

    # SM-02: codec per-tipe

    @staticmethod
    def encode_integer(value: int) -> bytes:
        # INTEGER -> bytes
        raise NotImplementedError

    @staticmethod
    def decode_integer(data: bytes) -> int:
        # bytes -> INTEGER
        raise NotImplementedError

    @staticmethod
    def encode_float(value: float) -> bytes:
        # FLOAT -> bytes
        raise NotImplementedError

    @staticmethod
    def decode_float(data: bytes) -> float:
        # bytes -> FLOAT
        raise NotImplementedError

    @staticmethod
    def encode_char(value: str, length: int) -> bytes:
        # CHAR -> bytes (dipad spasi)
        raise NotImplementedError

    @staticmethod
    def decode_char(data: bytes) -> str:
        # bytes -> CHAR (spasi kanan dibuang)
        raise NotImplementedError

    @staticmethod
    def encode_varchar(value: str, length: int) -> bytes:
        # VARCHAR -> bytes (prefix panjang)
        raise NotImplementedError

    @staticmethod
    def decode_varchar(data: bytes) -> str:
        # bytes -> VARCHAR
        raise NotImplementedError

    # SM-03: serialisasi record

    @staticmethod
    def record_size(columns: tuple[ColumnSchema, ...]) -> int:
        # Ukuran tetap satu record (byte)
        raise NotImplementedError

    @staticmethod
    def validate(schema: TableSchema, values: tuple) -> None:
        # Memastikan nilai valid terhadap skema
        raise NotImplementedError

    @staticmethod
    def encode(schema: TableSchema, values: tuple) -> bytes:
        # Serialisasi satu record menjadi bytes
        raise NotImplementedError

    @staticmethod
    def decode(schema: TableSchema, data: bytes) -> tuple:
        # Deserialisasi bytes menjadi tuple nilai
        raise NotImplementedError
