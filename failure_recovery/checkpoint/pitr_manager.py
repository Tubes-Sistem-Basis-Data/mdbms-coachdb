"""Point-in-time recovery ke waktu atau transaksi tertentu."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from common.errors import RecoveryError
from common.log import LogKind, LogRecord, RecoveryTarget
from common.transaction import ChangeOp
from failure_recovery.checkpoint.checkpoint_store import CheckpointStore
from failure_recovery.ports import LogReadInterface
from failure_recovery.recovery.redo_executor import RedoExecutor
from failure_recovery.results import RestoreResult


class PITRManager:
    """Pemulihan historis ke waktu atau transaksi tertentu."""

    def __init__(
        self,
        store: CheckpointStore,
        sm,
        log_reader: LogReadInterface,
    ) -> None:
        self._store = store
        self._sm = sm
        self._log_reader = log_reader

    def restore(
        self, target: RecoveryTarget, finalize: Callable[[RecoveryTarget, int], str],
    ) -> RestoreResult:
        """Pulihkan target dan pertahankan backup sampai checkpoint berhasil."""
        try:
            records = self._log_reader.read_log(-1)
            timestamp = target.timestamp
            if timestamp is not None and (
                not math.isfinite(timestamp) or timestamp > time.time()
                or (records and timestamp < min(record.timestamp for record in records))
            ):
                return RestoreResult(ok=False, message="Waktu target di luar rentang riwayat WAL")

            boundary = self._boundary(records, target)
            if boundary is None:
                return RestoreResult(
                    ok=False,
                    message=f"Catatan COMMIT untuk transaksi T{target.tx_id} tidak ditemukan di WAL",
                )

            committed = {
                record.tx_id
                for record in records
                if record.kind is LogKind.COMMIT
                and record.tx_id is not None
                and record.lsn <= boundary
            }
            aborted = {
                record.tx_id
                for record in records
                if record.kind is LogKind.ABORT
            }
            changes = [
                record
                for record in records
                if record.kind is LogKind.DATA
                and record.change is not None
                and record.tx_id not in aborted
            ]
            restore_markers = [record.lsn for record in records if record.kind is LogKind.RESTORE]
            latest_restore = max(restore_markers, default=0)
            checkpoint = self._store.find_latest_completed() if latest_restore > boundary else None
            same_target = (
                checkpoint is not None and checkpoint.checkpoint_lsn > latest_restore
                and checkpoint.restore_boundary_lsn == boundary
            )
            snapshot = None
            if latest_restore > boundary and not same_target:
                last_restore_before_target = max(
                    (lsn for lsn in restore_markers if lsn <= boundary), default=0,
                )
                snapshot = self._store.find_snapshot_by_lsn(last_restore_before_target, boundary)
                if snapshot is None or not callable(getattr(self._sm, "restore_from", None)):
                    raise RecoveryError("Snapshot untuk target pemulihan tidak tersedia")
                start_lsn = snapshot.checkpoint_lsn
                retained = sum(start_lsn < record.lsn <= boundary for record in records)
                if retained != boundary - start_lsn:
                    raise RecoveryError("Riwayat WAL dari snapshot ke target tidak lengkap")

            with self._preserve_data():
                if snapshot is None:
                    self._undo_suffix(changes, committed, latest_restore, boundary)
                else:
                    self._sm.restore_from(snapshot.snapshot_path)
                    RedoExecutor(self._sm).replay(
                        record for record in changes
                        if snapshot.checkpoint_lsn < record.lsn <= boundary
                        and record.tx_id in committed
                    )
                checkpoint_message = finalize(target, boundary)

            return RestoreResult(
                ok=True,
                message=f"Pemulihan ke {self._describe(target)} selesai; {checkpoint_message}",
            )
        except Exception as exc:
            return RestoreResult(ok=False, message=f"Pemulihan gagal: {exc}")

    @contextmanager
    def _preserve_data(self) -> Iterator[None]:
        snapshot_to = getattr(self._sm, "snapshot_to", None)
        restore_from = getattr(self._sm, "restore_from", None)
        if not callable(snapshot_to) or not callable(restore_from):
            yield
            return

        with TemporaryDirectory(prefix="frm-restore-", ignore_cleanup_errors=True) as directory:
            backup = str(Path(directory) / "before-restore")
            snapshot_to(backup)
            try:
                yield
            except Exception as exc:
                try:
                    restore_from(backup)
                except Exception as backup_error:
                    raise RecoveryError(
                        f"{exc}; gagal mengembalikan backup: {backup_error}"
                    ) from backup_error
                raise

    def _undo_suffix(
        self, changes: list[LogRecord], committed: set[int], after_lsn: int, target_lsn: int,
    ) -> None:
        for record in sorted(changes, key=lambda item: item.lsn, reverse=True):
            if record.lsn > after_lsn and (record.lsn > target_lsn or record.tx_id not in committed):
                self._undo(record)

    @staticmethod
    def _boundary(records: list[LogRecord], target: RecoveryTarget) -> int | None:
        if target.tx_id is not None:
            return max(
                (record.lsn for record in records
                 if record.tx_id == target.tx_id and record.kind is LogKind.COMMIT),
                default=None,
            )

        timestamp = target.timestamp
        return max(
            (record.lsn for record in records if record.timestamp <= timestamp),
            default=0,
        )

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
