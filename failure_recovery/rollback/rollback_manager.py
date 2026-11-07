"""Rollback transaksi lewat Storage Manager."""

from __future__ import annotations

from common.log import LogKind, LogRecord
from common.transaction import ChangeOp
from failure_recovery.results import RollbackResult
from failure_recovery.ports import (
    LogFlusher,
    LogReadInterface,
    LogWriter,
    RollbackInterface,
)


class RollbackManager(RollbackInterface):
    """Membatalkan transaksi dengan undo idempoten tanpa CLR.

    Keputusan desain #6: tidak ada catatan CLR. Idempotensi dicapai karena
    operasi insert/update/delete SM idempoten, sehingga undo aman diulang
    setelah crash tanpa perlu menandai perubahan yang sudah dibatalkan.
    """

    def __init__(
        self,
        log_writer: LogWriter,
        log_flusher: LogFlusher,
        log_reader: LogReadInterface,
        sm,
    ) -> None:
        self._log_writer = log_writer
        self._log_flusher = log_flusher
        self._log_reader = log_reader
        self._sm = sm

    def rollback(self, tx_id: int) -> RollbackResult | None:
        if self._is_already_aborted(tx_id):
            return RollbackResult.ALREADY_ABORT

        records = self._log_reader.backward_scan(tx_id)
        if not records:
            return RollbackResult.UNKNOWN_TX
        if any(record.kind is LogKind.COMMIT for record in records):
            return RollbackResult.COMMITTED

        abort_request = next(
            (record for record in records if record.kind is LogKind.ABORT_REQUEST),
            None,
        )
        if abort_request is None:
            request_lsn = self._log_writer.log_abort_request(tx_id)
            self._log_flusher.flush_log_until(request_lsn)

        for record in records:
            if record.kind is not LogKind.DATA or record.change is None:
                continue

            self._undo_record(record)

        abort_end_lsn = self._log_writer.log_abort_end(tx_id)
        self._log_flusher.flush_log_until(abort_end_lsn)
        return RollbackResult.DONE

    def _undo_record(self, record: LogRecord) -> None:
        change = record.change
        if record.kind is not LogKind.DATA or change is None:
            return

        if change.op is ChangeOp.INSERT:
            self._sm.delete(change.table, change.rid, None)
        elif change.op is ChangeOp.UPDATE:
            self._sm.update(change.table, change.rid, change.before, None)
        elif change.op is ChangeOp.DELETE:
            self._sm.insert(change.table, change.rid, change.before, None)

    def _is_already_aborted(self, tx_id: int) -> bool:
        return any(
            record.kind is LogKind.ABORT
            for record in self._log_reader.backward_scan(tx_id)
        )
