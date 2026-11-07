"""Penulis Write-Ahead Log."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from common.errors import LogError
from common.log import LogKind, LogRecord, RecoveryTarget
from common.storage import RID
from common.transaction import ChangeOp, DataChange
from failure_recovery.log import codec
from failure_recovery.ports import LogFlusher, LogWriter


class WalLogger(LogWriter, LogFlusher):
    """Penulis WAL berbasis file JSON per baris."""

    def __init__(self, log_path: str) -> None:
        self.log_path = str(log_path)
        self.next_lsn = 1
        self._prev: dict[int, int] = {}
        self._flushed_lsn = 0
        self._lock = threading.RLock()
        self._file = None
        codec.set_log_path(self.log_path)
        self._load_state()
        Path(self.log_path).parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self.log_path, "ab")

    def append(self, record: LogRecord) -> int:
        """Tulis satu record, isi lsn dan timestamp, kembalikan lsn-nya."""
        with self._lock:
            if not isinstance(record, LogRecord):
                raise LogError("record log tidak valid")
            if self._file is None:
                raise LogError("penulis log sudah ditutup")
            record.lsn = self.next_lsn
            record.timestamp = time.time()
            data = codec.serialize(record)
            if data is None:
                raise LogError(f"record LSN {record.lsn} tidak bisa diserialisasi")
            try:
                self._file.write(data)
                # flush tanpa fsync: cukup supaya pembaca lain melihat barisnya
                self._file.flush()
            except OSError as exc:
                raise LogError(f"gagal menulis log LSN {record.lsn}: {exc}") from exc
            self.next_lsn += 1
            if record.tx_id is not None:
                self._prev[record.tx_id] = record.lsn
            return record.lsn

    def flush_log_until(self, lsn: int) -> None:
        with self._lock:
            if lsn <= self._flushed_lsn:
                return
            self._persist(lsn)

    def close(self) -> None:
        with self._lock:
            if self._file is None:
                return
            try:
                self._file.flush()
                self._file.close()
            finally:
                self._file = None

    def _load_state(self) -> None:
        """Bangun next_lsn dan peta prev_lsn dari isi file; buang tulisan yang terputus."""
        path = Path(self.log_path)
        if not path.exists():
            return
        data = path.read_bytes()
        lines = data.splitlines(keepends=True)
        offset = 0
        last_good = 0
        for index, line in enumerate(lines):
            size = len(line)
            if line.strip():
                record = codec.parse(line)
                if record is None:
                    if any(rest.strip() for rest in lines[index + 1 :]):
                        raise LogError(f"baris {index + 1} rusak di tengah {path.name}")
                    break
                last_good = offset + size
                self.next_lsn = max(self.next_lsn, record.lsn + 1)
                if record.tx_id is not None:
                    self._prev[record.tx_id] = max(self._prev.get(record.tx_id, 0), record.lsn)
            offset += size
        else:
            if data and not data.endswith(b"\n"):
                with open(path, "ab") as handle:
                    handle.write(b"\n")
            return
        with open(path, "r+b") as handle:
            handle.truncate(last_good)

    def _prev_lsn(self, tx_id: int) -> int:
        with self._lock:
            return self._prev.get(tx_id, 0)

    def _persist(self, lsn: int) -> None:
        if self._file is None:
            raise LogError(f"penulis log sudah ditutup, flush LSN {lsn} gagal")
        try:
            self._file.flush()
            os.fsync(self._file.fileno())
        except (OSError, ValueError) as exc:
            raise LogError(f"gagal flush log sampai LSN {lsn}: {exc}") from exc
        # batasi ke lsn tertinggi yang benar-benar sudah tertulis, supaya
        # flush(50) saat baru ada LSN 1 tidak menandai LSN 2..50 sebagai permanen
        self._flushed_lsn = max(self._flushed_lsn, min(lsn, self.next_lsn - 1))

    def log_begin(self, tx_id: int) -> int:
        return self.append(LogRecord(LogKind.BEGIN, tx_id=tx_id))

    def log_write(
        self,
        tx_id: int,
        op: ChangeOp,
        rid: RID,
        before,
        after,
    ) -> int:
        change = DataChange(table=rid.table, rid=rid, op=op, before=before, after=after)
        return self.append(LogRecord(LogKind.DATA, tx_id=tx_id, change=change))

    def log_commit(self, tx_id: int) -> int:
        return self.append(LogRecord(LogKind.COMMIT, tx_id=tx_id))

    def log_abort_request(self, tx_id: int) -> int:
        return self.append(LogRecord(LogKind.ABORT_REQUEST, tx_id=tx_id))

    def log_abort_end(self, tx_id: int) -> int:
        return self.append(LogRecord(LogKind.ABORT, tx_id=tx_id))

    def log_clr(self, tx_id: int, rid: RID, before, after) -> int:
        # Keputusan desain #6 (FR-04): tidak ada CLR; undo idempoten.
        # Sengaja no-op agar kontrak LogWriter tetap kompatibel.
        return 0

    def log_checkpoint_begin(self) -> int:
        return self.append(LogRecord(LogKind.CHECKPOINT_BEGIN))

    def log_checkpoint_end(self) -> int:
        return self.append(LogRecord(LogKind.CHECKPOINT_END))

    def log_restore(self, target: RecoveryTarget) -> int:
        # LogKind belum punya RESTORE; kebutuhan PITR ditentukan di FR-05.
        return 0
