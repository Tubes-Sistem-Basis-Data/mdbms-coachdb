"""Point-in-time recovery ke waktu atau transaksi tertentu."""

from __future__ import annotations

from datetime import datetime

from common.log import LogKind, LogRecord, RecoveryTarget
from common.transaction import ChangeOp
from failure_recovery.results import RestoreResult
from failure_recovery.ports import (
    CheckpointQueryInterface,
    LogFlusher,
    LogReadInterface,
    LogWriter,
)


class PITRManager:
    """Pemulihan historis ke waktu atau transaksi tertentu."""

    def __init__(
        self,
        store: CheckpointQueryInterface,
        sm,
        log_writer: LogWriter,
        log_reader: LogReadInterface,
        log_flusher: LogFlusher | None = None,
    ) -> None:
        self._store = store
        self._sm = sm
        self._log_writer = log_writer
        self._log_reader = log_reader
        self._log_flusher = log_flusher

    def restore_to_time(
        self,
        target_time: datetime,
        target_tx_id: int | None = None,
    ) -> RestoreResult | None:
        if target_tx_id is not None:
            return self.restore_to_tx(target_tx_id)
        return self._restore(RecoveryTarget(timestamp=target_time.timestamp()))

    def restore_to_tx(self, target_tx_id: int) -> RestoreResult | None:
        return self._restore(RecoveryTarget(tx_id=target_tx_id))

    def _restore(self, target: RecoveryTarget) -> RestoreResult | None:
        try:
            records = self._log_reader.read_log(-1)
            boundary = self._boundary(records, target)
            if boundary is None:
                return RestoreResult(
                    ok=False,
                    message=f"Transaksi T{target.tx_id} tidak ditemukan di WAL",
                )

            committed = {
                record.tx_id
                for record in records
                if record.kind is LogKind.COMMIT
                and record.tx_id is not None
                and record.lsn <= boundary
            }
            undo_records = [
                record
                for record in records
                if record.kind is LogKind.DATA
                and record.change is not None
                and (record.lsn > boundary or record.tx_id not in committed)
            ]
            for record in sorted(undo_records, key=lambda item: item.lsn, reverse=True):
                self._undo(record)

            restore_lsn = self._log_writer.log_restore(target)
            self._flush(restore_lsn)
            return RestoreResult(
                ok=True,
                message=f"Pemulihan ke {self._describe(target)} selesai",
            )
        except Exception as exc:
            return RestoreResult(ok=False, message=f"Pemulihan gagal: {exc}")

    @staticmethod
    def _boundary(records: list[LogRecord], target: RecoveryTarget) -> int | None:
        if target.tx_id is not None:
            target_records = [
                record for record in records if record.tx_id == target.tx_id
            ]
            if not target_records:
                return None
            return max(record.lsn for record in target_records)

        timestamp = target.timestamp
        return max(
            (record.lsn for record in records if record.timestamp <= timestamp),
            default=0,
        )

    def _flush(self, lsn: int) -> None:
        if self._log_flusher is not None:
            self._log_flusher.flush_log_until(lsn)
            return
        flush = getattr(self._log_writer, "flush_log_until", None)
        if flush is not None:
            flush(lsn)

    def _undo(self, record: LogRecord) -> None:
        change = record.change
        if change is None:
            return

        if change.op is ChangeOp.INSERT:
            self._sm.delete(change.table, change.rid, None)
        elif change.op is ChangeOp.UPDATE:
            self._sm.update(change.table, change.rid, change.before, None)
        elif change.op is ChangeOp.DELETE:
            self._sm.insert(change.table, change.rid, change.before, None)

    @staticmethod
    def _describe(target: RecoveryTarget) -> str:
        if target.tx_id is not None:
            return f"T{target.tx_id}"
        return f"timestamp {target.timestamp}"
