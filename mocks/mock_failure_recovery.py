"""Mock Failure Recovery Manager: log in-memory."""

from __future__ import annotations

import copy
import threading
import time

from common.errors import LogError
from common.interfaces import ConcurrencyControlManager, FailureRecoveryManager, StorageManager
from common.log import (
    CheckpointResult,
    LogKind,
    LogRecord,
    LogResult,
    RecoveryResult,
    RecoveryTarget,
)
from common.transaction import ChangeOp, TxResult, TxStatus


class MockFailureRecoveryManager(FailureRecoveryManager):
    """FRM in-memory. LSN naik mulai 1, flush selalu berhasil, rollback lewat SM."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._log: list[LogRecord] = []
        self._next_lsn = 1
        self._sm: StorageManager | None = None
        self._ccm: ConcurrencyControlManager | None = None

    def write_log(self, record: LogRecord) -> LogResult:
        """Isi lsn dan timestamp, simpan salinan record."""
        with self._lock:
            record.lsn = self._next_lsn
            record.timestamp = time.time()
            self._next_lsn += 1
            self._log.append(copy.deepcopy(record))
        return LogResult(ok=True, lsn=record.lsn)

    def flush_log(self, up_to_lsn: int) -> bool:
        """Log in-memory dianggap selalu permanen."""
        return True

    def rollback(self, tx_id: int) -> TxResult:
        """Undo catatan DATA milik tx_id dengan urutan LSN terbalik."""
        if self._sm is None:
            raise LogError("mock FRM: set_dependencies belum dipanggil")
        sm = self._sm
        for rec in reversed(self.read_log(tx_id)):
            if rec.kind is not LogKind.DATA or rec.change is None:
                continue
            ch = rec.change
            exists = sm.fetch(ch.table, ch.rid) is not None
            match ch.op:
                case ChangeOp.INSERT:
                    if exists:
                        sm.delete(ch.table, ch.rid, None)
                case ChangeOp.DELETE:
                    if not exists:
                        sm.insert(ch.table, ch.rid, list(ch.before), None)
                case ChangeOp.UPDATE:
                    sm.update(ch.table, ch.rid, list(ch.before), None)
        return TxResult(tx_id, TxStatus.ABORTED, ok=True, message="rollback selesai")

    def checkpoint(self) -> CheckpointResult:
        """Catat CHECKPOINT_BEGIN dan CHECKPOINT_END tanpa menahan transaksi."""
        self.write_log(LogRecord(LogKind.CHECKPOINT_BEGIN))
        res = self.write_log(LogRecord(LogKind.CHECKPOINT_END))
        return CheckpointResult(ok=True, lsn=res.lsn, message="mock")

    def recover(self) -> RecoveryResult:
        """Tidak ada yang dipulihkan; kembalikan tx_id dan LSN terakhir di log."""
        with self._lock:
            tx_ids = [r.tx_id for r in self._log if r.tx_id is not None]
            last_lsn = self._next_lsn - 1
        return RecoveryResult(ok=True, last_tx_id=max(tx_ids, default=0),
                              last_lsn=last_lsn, message="mock")

    def recover_to(self, target: RecoveryTarget) -> RecoveryResult:
        """Tidak mengubah data."""
        result = self.recover()
        result.message = "mock"
        return result

    def read_log(self, tx_id: int | None = None) -> list[LogRecord]:
        """Salinan isi log, difilter per transaksi jika tx_id diisi."""
        with self._lock:
            records = [r for r in self._log if tx_id is None or r.tx_id == tx_id]
            return copy.deepcopy(records)

    def set_dependencies(self, sm: StorageManager, ccm: ConcurrencyControlManager) -> None:
        """Simpan referensi SM dan CCM."""
        self._sm = sm
        self._ccm = ccm
