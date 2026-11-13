from __future__ import annotations

from common.errors import LogWriteError
from common.interfaces import FailureRecoveryManager
from common.log import LogKind, LogRecord
from common.storage import RID
from common.transaction import ChangeOp, DataChange, TxResult


class FrmBridge:
    """Meneruskan peristiwa transaksi dan perubahan data ke FRM sesuai aturan WAL."""

    def __init__(self, frm: FailureRecoveryManager) -> None:
        self._frm = frm

    def log_begin(self, tx_id: int) -> int:
        return self._write(LogRecord(LogKind.BEGIN, tx_id=tx_id))

    def log_write(
        self, tx_id: int, op: ChangeOp, table: str, rid: RID, before, after
    ) -> int:
        change = DataChange(table=table, rid=rid, op=op, before=before, after=after)
        return self._write(LogRecord(LogKind.DATA, tx_id=tx_id, change=change))

    def log_commit(self, tx_id: int) -> int:
        lsn = self._write(LogRecord(LogKind.COMMIT, tx_id=tx_id))
        self._flush(lsn)
        return lsn

    def log_abort_request(self, tx_id: int) -> int:
        return self._write(LogRecord(LogKind.ABORT_REQUEST, tx_id=tx_id))

    def log_abort_end(self, tx_id: int) -> int:
        lsn = self._write(LogRecord(LogKind.ABORT, tx_id=tx_id))
        self._flush(lsn)
        return lsn

    def rollback(self, tx_id: int) -> TxResult:
        return self._frm.rollback(tx_id)

    def _write(self, record: LogRecord) -> int:
        result = self._frm.write_log(record)
        if not result.ok or result.lsn is None:
            raise LogWriteError(
                f"gagal mencatat {record.kind.value} T{record.tx_id}: {result.message}"
            )
        return result.lsn

    def _flush(self, lsn: int) -> None:
        if not self._frm.flush_log(lsn):
            raise LogWriteError(f"gagal flush log sampai LSN {lsn}")


class AbortCoordinator:
    def __init__(self, bridge: FrmBridge) -> None:
        self._bridge = bridge

    def abort(self, tx_id: int) -> TxResult:
        # FRM sudah menulis dan flush ABORT_REQUEST dan ABORT saat rollback.
        return self._bridge.rollback(tx_id)

    def mark_victim(self, tx_id: int) -> None:
        return None
