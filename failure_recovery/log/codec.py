"""Serialisasi dan parsing LogRecord untuk WAL."""

from __future__ import annotations

import json
from pathlib import Path

from common import config
from common.log import LogKind, LogRecord
from common.storage import RID
from common.transaction import NO_RECORD, ChangeOp, DataChange

_LOG_PATH = Path(config.DATA_DIR) / "wal.log"


def set_log_path(path: str | Path) -> None:
    global _LOG_PATH
    _LOG_PATH = Path(path)


def get_log_path() -> Path:
    return _LOG_PATH


def _encode_image(value) -> list | None:
    if value is NO_RECORD:
        return None
    return list(value)


def _decode_image(value) -> list | object:
    if value is None:
        return NO_RECORD
    return list(value)


def _encode_change(change: DataChange) -> dict:
    return {
        "table": change.table,
        "rid": [change.rid.table, change.rid.page_id, change.rid.slot],
        "op": change.op.value,
        "before": _encode_image(change.before),
        "after": _encode_image(change.after),
    }


def _decode_change(payload: dict) -> DataChange:
    rid = payload["rid"]
    if not isinstance(rid, (list, tuple)) or len(rid) != 3:
        raise ValueError("rid harus 3 elemen")
    return DataChange(
        table=str(payload["table"]),
        rid=RID(str(rid[0]), int(rid[1]), int(rid[2])),
        op=ChangeOp(payload["op"]),
        before=_decode_image(payload["before"]),
        after=_decode_image(payload["after"]),
    )


def serialize(record: LogRecord) -> bytes | None:
    """Satu baris JSON berakhiran newline. None kalau record tidak bisa diubah."""
    if not isinstance(record, LogRecord) or not isinstance(record.kind, LogKind):
        return None
    change = record.change
    if change is not None and not isinstance(change, DataChange):
        return None
    try:
        payload = {
            "kind": record.kind.value,
            "lsn": record.lsn,
            "timestamp": record.timestamp,
            "tx_id": record.tx_id,
            "active_tx": list(record.active_tx),
            "change": _encode_change(change) if change is not None else None,
        }
        text = json.dumps(payload, separators=(",", ":"))
    except (AttributeError, TypeError, ValueError):
        return None
    return text.encode("utf-8") + b"\n"


def parse(data: bytes | str) -> LogRecord | None:
    """Balikkan record dari satu baris. None kalau kosong, korup, atau bukan JSON."""
    if isinstance(data, bytes):
        try:
            data = data.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(data, str):
        return None
    text = data.strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
        if not isinstance(payload, dict):
            return None
        change = payload.get("change")
        return LogRecord(
            kind=LogKind(payload["kind"]),
            lsn=int(payload["lsn"]),
            timestamp=float(payload["timestamp"]),
            tx_id=payload["tx_id"],
            active_tx=list(payload["active_tx"]),
            change=_decode_change(change) if change is not None else None,
        )
    except (KeyError, TypeError, ValueError):
        return None
