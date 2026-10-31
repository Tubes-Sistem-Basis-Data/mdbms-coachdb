"""Serialisasi dan parsing LogRecord untuk WAL."""

from __future__ import annotations

from pathlib import Path

from common import config
from common.log import LogRecord
from common.transaction import RECORD_ABSENT

_LOG_PATH = Path(config.DATA_DIR) / "wal.log"


def set_log_path(path: str | Path) -> None:
    global _LOG_PATH
    _LOG_PATH = Path(path)


def get_log_path() -> Path:
    return _LOG_PATH


def _encode_image(value) -> list | None:
    if value is RECORD_ABSENT:
        return None
    return list(value)


def _decode_image(value) -> tuple | object:
    if value is None:
        return RECORD_ABSENT
    return tuple(value)


def serialize(record: LogRecord) -> bytes | None:
    return None


def parse(data: bytes | str) -> LogRecord | None:
    return None
