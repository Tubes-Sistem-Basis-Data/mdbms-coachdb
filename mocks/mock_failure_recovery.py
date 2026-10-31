"""Mock Failure Recovery Manager: log in-memory."""

from __future__ import annotations

import copy
import threading
from datetime import datetime

from common.errors import LogError
from common.interfaces import IFailureRecovery
from common.log import (
    CheckpointResult,
    LogKind,
    LogRecord,
    RestoreResult,
)
from common.storage import RID
from common.transaction import ChangeOp, RollbackResult, TxStatus
from failure_recovery.recovery.analysis import RecoveryReport, RecoveryStatus


class MockFailureRecoveryManager(IFailureRecovery):
    """FRM in-memory. LSN naik mulai 1, flush selalu berhasil, rollback lewat SM."""

    def __init__(self) -> None:
        self._lsn_lock = threading.Lock()
        self._next_lsn = 1
        self._log: list[LogRecord] = []
        self._sm = None
        self._ccm = None

    def _append(self, record: LogRecord) -> int:
        with self._lsn_lock:
            record.lsn = self._next_lsn
            record.timestamp = datetime.now().astimezone()
            if record.prev_lsn < 0 and record.tx_id >= 0:
                previous = [
                    item.lsn for item in self._log if item.tx_id == record.tx_id
                ]
                record.prev_lsn = max(previous, default=-1)
            self._next_lsn += 1
            self._log.append(copy.deepcopy(record))
            return record.lsn

    def attach_storage(self, sm) -> None:
        self._sm = sm

    def attach_ccm(self, ccm) -> None:
        self._ccm = ccm

    def log_begin(self, tx_id: int) -> int:
        return self._append(LogRecord(tx_id=tx_id, kind=LogKind.BEGIN))

    def log_write(
        self,
        tx_id: int,
        op: ChangeOp,
        table: str,
        rid: RID,
        before,
        after,
    ) -> int:
        kind = {
            ChangeOp.INSERT: LogKind.INSERT,
            ChangeOp.UPDATE: LogKind.UPDATE,
            ChangeOp.DELETE: LogKind.DELETE,
        }[op]
        return self._append(
            LogRecord(
                tx_id=tx_id,
                kind=kind,
                table=table,
                rid=rid,
                before=before,
                after=after,
            )
        )

    def log_commit(self, tx_id: int) -> int:
        return self._append(LogRecord(tx_id=tx_id, kind=LogKind.COMMIT))

    def rollback(self, tx_id: int) -> RollbackResult:
        """Undo catatan perubahan milik tx_id dengan urutan LSN terbalik."""
        if self._sm is None:
            raise LogError("mock FRM: attach_storage belum dipanggil")
        records = [record for record in self._log if record.tx_id == tx_id]
        if not records:
            return RollbackResult.UNKNOWN_TX
        if any(record.kind is LogKind.COMMIT for record in records):
            return RollbackResult.COMMITTED
        if any(record.kind is LogKind.ABORT_END for record in records):
            return RollbackResult.ALREADY_ABORT

        self._append(LogRecord(tx_id=tx_id, kind=LogKind.ABORT_REQUEST))
        for record in reversed(records):
            if record.kind is LogKind.INSERT:
                self._sm.undo_insert(record.table, record.rid)
            elif record.kind is LogKind.UPDATE:
                self._sm.undo_update(record.table, record.rid, record.before)
            elif record.kind is LogKind.DELETE:
                self._sm.undo_delete(record.table, record.rid, record.before)
            else:
                continue
            self._append(
                LogRecord(
                    tx_id=tx_id,
                    kind=LogKind.CLR,
                    table=record.table,
                    rid=record.rid,
                    before=record.after,
                    after=record.before,
                )
            )
        self._append(LogRecord(tx_id=tx_id, kind=LogKind.ABORT_END))
        return RollbackResult.DONE

    def flush_log_until(self, lsn: int) -> None:
        return None

    def show_log(self, tx_id: int | None) -> list[LogRecord]:
        with self._lsn_lock:
            records = [
                copy.deepcopy(record)
                for record in self._log
                if tx_id is None or record.tx_id == tx_id
            ]
        return records

    def checkpoint(self) -> CheckpointResult:
        self._append(LogRecord(tx_id=-1, kind=LogKind.CHECKPOINT_BEGIN))
        lsn = self._append(LogRecord(tx_id=-1, kind=LogKind.CHECKPOINT_END))
        return CheckpointResult(ok=True, lsn=lsn, message="mock")

    def restore_to(
        self,
        target_time: datetime | None,
        target_tx_id: int | None,
    ) -> RestoreResult:
        return RestoreResult(ok=True, message="mock")

    def recover(self) -> RecoveryReport:
        with self._lsn_lock:
            records = list(self._log)
        tx_state: dict[int, TxStatus] = {}
        for record in records:
            if record.tx_id < 0:
                continue
            if record.kind is LogKind.BEGIN:
                tx_state[record.tx_id] = TxStatus.ACTIVE
            elif record.kind is LogKind.COMMIT:
                tx_state[record.tx_id] = TxStatus.COMMITTED
            elif record.kind is LogKind.ABORT_END:
                tx_state[record.tx_id] = TxStatus.ABORTED
            else:
                tx_state.setdefault(record.tx_id, TxStatus.ACTIVE)
        return RecoveryReport(
            status=RecoveryStatus.SUCCESS,
            start_lsn=0,
            committed_tx=[
                tx_id for tx_id, status in tx_state.items() if status is TxStatus.COMMITTED
            ],
            aborted_tx=[
                tx_id for tx_id, status in tx_state.items() if status is TxStatus.ABORTED
            ],
            max_tx_id=max(tx_state, default=0),
            errors=[],
        )

    def close(self) -> None:
        return None
