"""FRM driver scenarios using real Storage Manager and Concurrency Control."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from common.catalog import ColumnSchema, DataType
from common.log import LogKind, RecoveryTarget
from common.storage import RID
from common.transaction import (
    NO_RECORD,
    AccessStatus,
    Action,
    ChangeOp,
    DataChange,
    ProtocolType,
    ResourceId,
    TxStatus,
)
from concurrency_control import CCManager
from drivers.frm_scenarios import FRMCaseContext, FRMTestScenario
from failure_recovery import FailureRecoveryManager
from storage_manager.facade import StorageManager


PROTOCOLS = {
    "lock": ProtocolType.LOCK_BASED,
    "timestamp": ProtocolType.TIMESTAMP_BASED,
}


class RealFRMContext(FRMCaseContext):
    def __init__(
        self,
        temporary_dir: TemporaryDirectory,
        root: Path,
        sm: StorageManager,
        frm: FailureRecoveryManager,
        ccm: CCManager,
        protocol: ProtocolType,
    ) -> None:
        super().__init__(temporary_dir, root, frm)
        self.sm = sm
        self.ccm = ccm
        self.protocol = protocol


class RealFRMScenarioCatalog:
    def __init__(self) -> None:
        definitions = (
            (
                "real-rollback", "Abort through real CCM restores the table",
                self._rollback,
                {"abort_ok": True, "rows": [], "log_tail": ["ABORT_REQUEST", "ABORT"]},
            ),
            (
                "real-checkpoint", "Checkpoint persists WAL markers and metadata",
                self._checkpoint,
                {"checkpoint_ok": True, "rows": [[1, "keep"]],
                 "markers": ["CHECKPOINT_BEGIN", "CHECKPOINT_END"],
                 "master_completed": True, "master_lsn_matches": True},
            ),
            (
                "real-pitr", "PITR restores the state after T1",
                self._pitr,
                {"restore_ok": True, "rows": [[1, "old"]],
                 "restore_logged": True, "checkpoint_after_restore": True},
            ),
            (
                "real-multi-tx-recovery", "Recovery redoes commits and undoes an active transaction",
                self._multi_tx_recovery,
                {"recover_ok": True, "rows": [[1, "redo"], [3, "new"]],
                 "active_tx_aborted": True, "last_tx_id_valid": True},
            ),
        )
        self.cases = [
            FRMTestScenario(
                name=f"{name}-{protocol}",
                setup=lambda protocol=protocol: self._setup(protocol),
                actions=[action],
                expected=expected,
                cleanup=self._cleanup,
                requirement="FR-09",
                description=f"{description} ({protocol})",
            )
            for protocol in PROTOCOLS
            for name, description, action, expected in definitions
        ]

    @staticmethod
    def _create_table(sm: StorageManager) -> None:
        columns = (
            ColumnSchema("id", DataType.INTEGER, nullable=False),
            ColumnSchema("name", DataType.VARCHAR, length=50),
        )
        # TODO: use StorageManager.create_table when its public API is implemented.
        sm._catalog.create_table("student", columns, ("id",), ())

    @staticmethod
    def _open_runtime(root: Path, protocol: ProtocolType) -> tuple[StorageManager, FailureRecoveryManager, CCManager]:
        sm = StorageManager(str(root))
        frm = None
        try:
            frm = FailureRecoveryManager(log_path=str(root / "wal.log"))
            ccm = CCManager(frm, protocol=protocol)
            frm.set_dependencies(sm, ccm)
            sm.set_log_flusher(frm.flush_log)
            return sm, frm, ccm
        except Exception:
            try:
                sm.close()
            finally:
                if frm is not None:
                    frm.close()
            raise

    @staticmethod
    def _close_runtime(sm: StorageManager, frm: FailureRecoveryManager) -> None:
        try:
            sm.close()
        finally:
            frm.close()

    def _setup(self, protocol_name: str) -> RealFRMContext:
        temporary_dir = TemporaryDirectory(prefix=f"coachdb-frm-real-{protocol_name}-")
        root = Path(temporary_dir.name)
        try:
            protocol = PROTOCOLS[protocol_name]
            sm, frm, ccm = self._open_runtime(root, protocol)
            try:
                self._create_table(sm)
            except Exception:
                self._close_runtime(sm, frm)
                raise
            return RealFRMContext(temporary_dir, root, sm, frm, ccm, protocol)
        except Exception:
            temporary_dir.cleanup()
            raise

    @classmethod
    def _cleanup(cls, context: RealFRMContext) -> None:
        try:
            cls._close_runtime(context.sm, context.frm)
        finally:
            context.temporary_dir.cleanup()

    @classmethod
    def _restart(cls, context: RealFRMContext):
        cls._close_runtime(context.sm, context.frm)
        sm, frm, ccm = cls._open_runtime(context.root, context.protocol)
        context.sm, context.frm, context.ccm = sm, frm, ccm
        recovery = frm.recover()
        if recovery.ok:
            ccm.initialize(recovery.last_tx_id)
        return recovery

    @staticmethod
    def _rows(sm: StorageManager) -> list[list]:
        return sorted((list(row.values) for row in sm.scan("student")), key=lambda values: values[0])

    @staticmethod
    def _require(condition: bool, message: str) -> None:
        if not condition:
            raise RuntimeError(message)

    def _insert(self, context: RealFRMContext, tx_id: int, values: list, apply: bool = True) -> RID:
        rid = context.sm.allocate_rid("student")
        change = DataChange("student", rid, ChangeOp.INSERT, NO_RECORD, values)
        response = context.ccm.validate_object(
            tx_id, ResourceId("student", rid), Action.WRITE, change
        )
        self._require(response.status is AccessStatus.ALLOWED and response.lsn is not None,
                      f"T{tx_id} INSERT was not allowed: {response.message}")
        if apply:
            context.sm.insert("student", rid, values, response.lsn)
        return rid

    def _update(
        self, context: RealFRMContext, tx_id: int, rid: RID,
        before: list, after: list, apply: bool = True,
    ) -> None:
        change = DataChange("student", rid, ChangeOp.UPDATE, before, after)
        response = context.ccm.validate_object(
            tx_id, ResourceId("student", rid), Action.WRITE, change
        )
        self._require(response.status is AccessStatus.ALLOWED and response.lsn is not None,
                      f"T{tx_id} UPDATE was not allowed: {response.message}")
        if apply:
            context.sm.update("student", rid, after, response.lsn)

    def _commit(self, context: RealFRMContext, tx_id: int) -> None:
        result = context.ccm.commit(tx_id)
        self._require(result.ok and result.status is TxStatus.COMMITTED,
                      f"T{tx_id} commit failed: {result.message}")

    def _rollback(self, context: RealFRMContext) -> dict:
        tx_id = context.ccm.begin_transaction()
        self._insert(context, tx_id, [1, "temporary"])
        result = context.ccm.abort(tx_id)
        context.notes.append("Real CCM abort invoked FRM rollback")
        return {
            "abort_ok": result.ok and result.status is TxStatus.ABORTED,
            "rows": self._rows(context.sm),
            "log_tail": [record.kind.value for record in context.frm.read_log(tx_id)[-2:]],
        }

    def _checkpoint(self, context: RealFRMContext) -> dict:
        tx_id = context.ccm.begin_transaction()
        self._insert(context, tx_id, [1, "keep"])
        self._commit(context, tx_id)
        result = context.frm.checkpoint()
        master = context.root / "wal.checkpoint.json"
        metadata = json.loads(master.read_text(encoding="utf-8")) if master.exists() else {}
        context.notes.append("Real SM flush and CCM checkpoint coordination completed")
        return {
            "checkpoint_ok": result.ok,
            "rows": self._rows(context.sm),
            "markers": [record.kind.value for record in context.frm.read_log()[-2:]],
            "master_completed": metadata.get("completed") is True,
            "master_lsn_matches": metadata.get("checkpoint_lsn") == result.lsn,
        }

    def _pitr(self, context: RealFRMContext) -> dict:
        t1 = context.ccm.begin_transaction()
        rid = self._insert(context, t1, [1, "old"])
        self._commit(context, t1)
        checkpoint = context.frm.checkpoint()
        self._require(checkpoint.ok, f"Checkpoint before PITR failed: {checkpoint.message}")

        t2 = context.ccm.begin_transaction()
        self._update(context, t2, rid, [1, "old"], [1, "new"])
        self._commit(context, t2)
        result = context.frm.recover_to(RecoveryTarget(tx_id=t1))
        records = context.frm.read_log()
        restore_at = [index for index, record in enumerate(records) if record.kind is LogKind.RESTORE]
        context.notes.append("PITR restored a real Storage Manager snapshot")
        return {
            "restore_ok": result.ok,
            "rows": self._rows(context.sm),
            "restore_logged": bool(restore_at),
            "checkpoint_after_restore": bool(restore_at) and any(
                record.kind is LogKind.CHECKPOINT_END for record in records[restore_at[-1] + 1:]
            ),
        }

    def _multi_tx_recovery(self, context: RealFRMContext) -> dict:
        context.sm.bulk_load("student", [[3, "old"]])
        base_rid = next(row.rid for row in context.sm.scan("student") if row.values[0] == 3)

        t1 = context.ccm.begin_transaction()
        self._insert(context, t1, [1, "redo"], apply=False)
        self._commit(context, t1)

        t2 = context.ccm.begin_transaction()
        self._insert(context, t2, [2, "undo"])

        t3 = context.ccm.begin_transaction()
        self._update(context, t3, base_rid, [3, "old"], [3, "new"], apply=False)
        self._commit(context, t3)

        context.sm.flush()
        recovery = self._restart(context)
        context.notes.append("Recovery redid T1/T3 and undid active T2 with real SM")
        return {
            "recover_ok": recovery.ok,
            "rows": self._rows(context.sm),
            "active_tx_aborted": any(
                record.kind is LogKind.ABORT for record in context.frm.read_log(t2)
            ),
            "last_tx_id_valid": recovery.last_tx_id >= t3,
        }
