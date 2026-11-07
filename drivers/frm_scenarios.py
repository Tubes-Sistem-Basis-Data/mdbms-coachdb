from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from common.interfaces import FailureRecoveryManager as FailureRecoveryManagerInterface
from common.log import LogKind, LogRecord, RecoveryTarget
from common.storage import RID
from common.transaction import NO_RECORD, ChangeOp, DataChange, TxStatus


@dataclass
class FRMCaseContext:
    temporary_dir: tempfile.TemporaryDirectory
    root: Path
    frm: FailureRecoveryManagerInterface
    notes: list[str] = field(default_factory=list)


@dataclass
class FRMTestScenario:
    """One case following the FRMTestScenario design in frmDiagram.svg."""

    name: str
    setup: Callable[[], FRMCaseContext]
    actions: list[Callable[[FRMCaseContext], object]]
    expected: object
    cleanup: Callable[[FRMCaseContext], None]
    requirement: str = ""
    description: str = ""


@dataclass
class FRMTestResult:
    """Actual result and verdict for one case, following the diagram."""

    name: str
    passed: bool
    actual: object
    message: str
    skipped: bool = False


class ScenarioFailure(Exception):
    """A scenario action failed before its result could be compared."""


class FeatureNotReady(Exception):
    """The facade explicitly reports an unimplemented recovery operation."""


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise ScenarioFailure(message)


class DemoStorageManager:
    """In-memory data for observing FRM undo and flush effects."""

    def __init__(self) -> None:
        self.rows: dict[RID, list] = {}
        self.calls: list[str] = []

    def insert(self, table: str, rid: RID, values: list, lsn: int | None) -> None:
        self.calls.append("insert")
        self.rows[rid] = list(values)

    def update(self, table: str, rid: RID, values: list, lsn: int | None) -> None:
        self.calls.append("update")
        self.rows[rid] = list(values)

    def delete(self, table: str, rid: RID, lsn: int | None) -> None:
        self.calls.append("delete")
        self.rows.pop(rid, None)

    def flush(self) -> None:
        self.calls.append("flush")


class DemoConcurrencyControl:
    """Record checkpoint coordination with CCM."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def pause_new_transactions(self) -> None:
        self.calls.append("pause")

    def wait_until_idle(self) -> None:
        self.calls.append("wait")

    def resume_transactions(self) -> None:
        self.calls.append("resume")


class _FRMScenarioCatalog:
    """Define isolated FRM cases, fixtures, actions, and expected results."""

    def __init__(self, frm_factory: Callable[[str], FailureRecoveryManagerInterface]) -> None:
        self._frm_factory = frm_factory
        self.cases: list[FRMTestScenario] = [
            self._case("wal", "FR-01/02", "WAL records data changes and transaction events",
                       self._wal, {
                           "lsns": [1, 2, 3], "kinds": ["BEGIN", "DATA", "COMMIT"],
                           "timestamps_set": True, "change_matches": True, "flushed": True,
                       }),
            self._case("wal-reopen", "FR-01/02", "WAL remains readable after reopening FRM",
                       self._wal_reopen, {"kinds": ["BEGIN", "COMMIT"], "next_lsn": 3}),
            self._case("read-filter", "FR-03", "Read all logs and filter by transaction",
                       self._read_filter, {"all": 3, "tx1": ["BEGIN", "COMMIT"],
                                           "tx2": ["BEGIN"], "unknown": 0}),
            self._case("rollback-crud", "FR-04", "Rollback undoes INSERT, UPDATE, and DELETE",
                       self._rollback_crud, {
                           "ok": True, "status": "ABORTED", "inserted": None,
                           "updated": [2, "old"], "deleted": [3, "old"],
                           "undo_calls": ["insert", "update", "delete"],
                           "log_tail": ["ABORT_REQUEST", "ABORT"],
                       }),
            self._case("rollback-repeat", "FR-04", "Repeated rollback does not repeat changes",
                       self._rollback_repeat, {
                           "first_ok": True, "second_ok": True, "second_status": "ABORTED",
                           "first_undid_once": True, "unchanged_after_second": True,
                       }),
            self._case("rollback-committed", "FR-04", "Committed transactions cannot be rolled back",
                       self._rollback_committed, {
                           "ok": False, "status": "COMMITTED", "unchanged": True,
                       }),
            self._case("checkpoint", "FR-06", "Checkpoint writes markers and metadata",
                       self._checkpoint, {
                           "ok": True, "lsn": 4,
                           "markers": ["CHECKPOINT_BEGIN", "CHECKPOINT_END"],
                           "ccm_calls": ["pause", "wait", "resume"],
                           "sm_calls": ["flush"], "metadata_matches": True,
                       }),
            self._case("multi-tx", "FR-04", "Rolling back T1 leaves T2 unchanged",
                       self._multi_tx, {
                           "ok": True, "status": "ABORTED", "t1": None, "t2": [2],
                           "t2_log": ["BEGIN", "DATA", "COMMIT"],
                       }),
            self._case("pitr", "FR-05", "Recover to a specific time or transaction",
                       self._pitr, {"ok": True, "restored": [1, "old"],
                                    "restore_logged": True, "checkpoint_after_restore": True}),
            self._case("crash-recovery", "FR-07", "Recover automatically after a crash",
                       self._crash_recovery, {"ok": True, "restored": [1],
                                              "last_tx_id_valid": True}),
            self._case("multi-tx-recovery", "FR-08", "Recover multiple transactions after a crash",
                       self._multi_tx_recovery, {"ok": True, "committed_insert": [1],
                                                 "uncommitted_insert": None,
                                                 "committed_update": [3, "new"],
                                                 "last_tx_id_valid": True}),
        ]

    def _case(self, name: str, requirement: str, description: str,
              action: Callable[[FRMCaseContext], object], expected: object) -> FRMTestScenario:
        return FRMTestScenario(name, self._setup, [action], expected, self._cleanup,
                               requirement, description)

    def _setup(self) -> FRMCaseContext:
        temporary_dir = tempfile.TemporaryDirectory(prefix="coachdb-frm-driver-")
        root = Path(temporary_dir.name)
        try:
            frm = self._frm_factory(str(root / "wal.log"))
        except Exception:
            temporary_dir.cleanup()
            raise
        return FRMCaseContext(temporary_dir, root, frm)

    @staticmethod
    def _cleanup(context: FRMCaseContext) -> None:
        try:
            context.frm.close()
        finally:
            context.temporary_dir.cleanup()

    @staticmethod
    def _write(frm: FailureRecoveryManagerInterface, kind: LogKind, tx_id: int | None = None,
               change: DataChange | None = None) -> int:
        result = frm.write_log(LogRecord(kind, tx_id=tx_id, change=change))
        _check(result.ok and result.lsn is not None, f"write_log({kind.value}) failed: {result.message}")
        return result.lsn

    @staticmethod
    def _change(rid: RID, op: ChangeOp, before, after) -> DataChange:
        return DataChange(rid.table, rid, op, before, after)

    @staticmethod
    def _require_recovery(operation: str, message: str) -> None:
        lowered = message.casefold()
        if "belum diimplementasikan" in lowered or "not implemented" in lowered:
            raise FeatureNotReady(f"{operation} is not implemented yet")

    def _reopen(self, context: FRMCaseContext) -> FailureRecoveryManagerInterface:
        context.frm.close()
        context.frm = self._frm_factory(str(context.root / "wal.log"))
        return context.frm

    def _wal(self, context: FRMCaseContext) -> dict:
        frm = context.frm
        rid = RID("student", 0, 1)
        change = self._change(rid, ChangeOp.INSERT, NO_RECORD, [1, "Ana"])
        lsns = [self._write(frm, LogKind.BEGIN, 1),
                self._write(frm, LogKind.DATA, 1, change),
                self._write(frm, LogKind.COMMIT, 1)]
        flushed = frm.flush_log(lsns[-1])
        records = frm.read_log()
        context.notes.append("BEGIN -> DATA -> COMMIT; flush WAL")
        return {
            "lsns": lsns, "kinds": [r.kind.value for r in records],
            "timestamps_set": all(r.timestamp > 0 for r in records),
            "change_matches": len(records) > 1 and records[1].change == change,
            "flushed": flushed,
        }

    def _wal_reopen(self, context: FRMCaseContext) -> dict:
        frm = context.frm
        self._write(frm, LogKind.BEGIN, 7)
        last_lsn = self._write(frm, LogKind.COMMIT, 7)
        _check(frm.flush_log(last_lsn), "flush before reopening failed")
        frm.close()
        reopened = self._frm_factory(str(context.root / "wal.log"))
        try:
            kinds = [r.kind.value for r in reopened.read_log(7)]
            next_lsn = self._write(reopened, LogKind.BEGIN, 8)
        finally:
            reopened.close()
        context.notes.append("Log persists and LSN continues after reopening")
        return {"kinds": kinds, "next_lsn": next_lsn}

    def _read_filter(self, context: FRMCaseContext) -> dict:
        frm = context.frm
        self._write(frm, LogKind.BEGIN, 1)
        self._write(frm, LogKind.BEGIN, 2)
        self._write(frm, LogKind.COMMIT, 1)
        context.notes.append("Read all logs and filter T1, T2, and T99")
        return {
            "all": len(frm.read_log()),
            "tx1": [r.kind.value for r in frm.read_log(1)],
            "tx2": [r.kind.value for r in frm.read_log(2)],
            "unknown": len(frm.read_log(99)),
        }

    def _rollback_crud(self, context: FRMCaseContext) -> dict:
        frm = context.frm
        sm = DemoStorageManager()
        frm.set_dependencies(sm, DemoConcurrencyControl())
        inserted = RID("student", 0, 1)
        updated = RID("student", 0, 2)
        deleted = RID("student", 0, 3)
        sm.rows = {inserted: [1, "new"], updated: [2, "new"]}
        self._write(frm, LogKind.BEGIN, 1)
        self._write(frm, LogKind.DATA, 1, self._change(inserted, ChangeOp.INSERT, NO_RECORD, [1, "new"]))
        self._write(frm, LogKind.DATA, 1, self._change(updated, ChangeOp.UPDATE, [2, "old"], [2, "new"]))
        self._write(frm, LogKind.DATA, 1, self._change(deleted, ChangeOp.DELETE, [3, "old"], NO_RECORD))
        result = frm.rollback(1)
        context.notes.append("Undo INSERT, UPDATE, and DELETE in reverse order")
        return {
            "ok": result.ok, "status": result.status.value,
            "inserted": sm.rows.get(inserted), "updated": sm.rows.get(updated),
            "deleted": sm.rows.get(deleted), "undo_calls": sm.calls,
            "log_tail": [r.kind.value for r in frm.read_log(1)[-2:]],
        }

    def _rollback_repeat(self, context: FRMCaseContext) -> dict:
        frm = context.frm
        sm = DemoStorageManager()
        frm.set_dependencies(sm, DemoConcurrencyControl())
        rid = RID("student", 0, 1)
        sm.rows[rid] = [1]
        self._write(frm, LogKind.BEGIN, 1)
        self._write(frm, LogKind.DATA, 1, self._change(rid, ChangeOp.INSERT, NO_RECORD, [1]))
        first = frm.rollback(1)
        calls = list(sm.calls)
        log_count = len(frm.read_log(1))
        first_undid_once = rid not in sm.rows and calls == ["delete"]
        second = frm.rollback(1)
        context.notes.append("The second rollback does not repeat SM operations or log entries")
        return {
            "first_ok": first.ok, "second_ok": second.ok,
            "second_status": second.status.value, "first_undid_once": first_undid_once,
            "unchanged_after_second": sm.calls == calls and len(frm.read_log(1)) == log_count,
        }

    def _rollback_committed(self, context: FRMCaseContext) -> dict:
        frm = context.frm
        sm = DemoStorageManager()
        frm.set_dependencies(sm, DemoConcurrencyControl())
        self._write(frm, LogKind.BEGIN, 1)
        self._write(frm, LogKind.COMMIT, 1)
        before = frm.read_log(1)
        result = frm.rollback(1)
        context.notes.append("Rollback of a committed transaction is rejected")
        return {"ok": result.ok, "status": result.status.value,
                "unchanged": sm.calls == [] and frm.read_log(1) == before}

    def _checkpoint(self, context: FRMCaseContext) -> dict:
        frm = context.frm
        sm = DemoStorageManager()
        ccm = DemoConcurrencyControl()
        frm.set_dependencies(sm, ccm)
        self._write(frm, LogKind.BEGIN, 9)
        self._write(frm, LogKind.COMMIT, 9)
        result = frm.checkpoint()
        records = frm.read_log()
        master = context.root / "wal.checkpoint.json"
        metadata = json.loads(master.read_text(encoding="utf-8")) if master.exists() else {}
        context.notes.append(f"Checkpoint LSN {result.lsn}; SM flush and persistent metadata verified")
        return {
            "ok": result.ok, "lsn": result.lsn,
            "markers": [r.kind.value for r in records[-2:]],
            "ccm_calls": ccm.calls, "sm_calls": sm.calls,
            "metadata_matches": metadata.get("completed") is True
                and metadata.get("checkpoint_lsn") == result.lsn
                and metadata.get("max_tx_id") == 9,
        }

    def _multi_tx(self, context: FRMCaseContext) -> dict:
        frm = context.frm
        sm = DemoStorageManager()
        frm.set_dependencies(sm, DemoConcurrencyControl())
        rid1 = RID("student", 0, 1)
        rid2 = RID("student", 0, 2)
        sm.rows = {rid1: [1], rid2: [2]}
        self._write(frm, LogKind.BEGIN, 1)
        self._write(frm, LogKind.BEGIN, 2)
        self._write(frm, LogKind.DATA, 1, self._change(rid1, ChangeOp.INSERT, NO_RECORD, [1]))
        self._write(frm, LogKind.DATA, 2, self._change(rid2, ChangeOp.INSERT, NO_RECORD, [2]))
        self._write(frm, LogKind.COMMIT, 2)
        result = frm.rollback(1)
        context.notes.append("T1 is rolled back; committed T2 remains intact")
        return {
            "ok": result.ok, "status": result.status.value,
            "t1": sm.rows.get(rid1), "t2": sm.rows.get(rid2),
            "t2_log": [r.kind.value for r in frm.read_log(2)],
        }

    def _pitr(self, context: FRMCaseContext) -> dict:
        frm = context.frm
        sm = DemoStorageManager()
        frm.set_dependencies(sm, DemoConcurrencyControl())
        rid = RID("student", 0, 1)
        self._write(frm, LogKind.BEGIN, 1)
        first_lsn = self._write(frm, LogKind.DATA, 1,
                                self._change(rid, ChangeOp.INSERT, NO_RECORD, [1, "old"]))
        sm.insert(rid.table, rid, [1, "old"], first_lsn)
        self._write(frm, LogKind.COMMIT, 1)
        checkpoint = frm.checkpoint()
        _check(checkpoint.ok, f"checkpoint before point-in-time recovery failed: {checkpoint.message}")
        self._write(frm, LogKind.BEGIN, 2)
        update_lsn = self._write(frm, LogKind.DATA, 2,
                                 self._change(rid, ChangeOp.UPDATE, [1, "old"], [1, "new"]))
        sm.update(rid.table, rid, [1, "new"], update_lsn)
        self._write(frm, LogKind.COMMIT, 2)

        result = frm.recover_to(RecoveryTarget(tx_id=1))
        self._require_recovery("recover_to()", result.message)
        records = frm.read_log()
        restore_positions = [index for index, record in enumerate(records)
                             if record.kind is LogKind.RESTORE]
        context.notes.append("Restore the state after T1, before T2's update")
        return {
            "ok": result.ok,
            "restored": sm.rows.get(rid),
            "restore_logged": bool(restore_positions),
            "checkpoint_after_restore": bool(restore_positions)
                and any(record.kind is LogKind.CHECKPOINT_END
                        for record in records[restore_positions[-1] + 1:]),
        }

    def _crash_recovery(self, context: FRMCaseContext) -> dict:
        frm = context.frm
        sm = DemoStorageManager()
        rid = RID("student", 0, 1)
        self._write(frm, LogKind.BEGIN, 1)
        self._write(frm, LogKind.DATA, 1,
                    self._change(rid, ChangeOp.INSERT, NO_RECORD, [1]))
        commit_lsn = self._write(frm, LogKind.COMMIT, 1)
        _check(frm.flush_log(commit_lsn), "flush before simulated crash failed")
        # The committed row has not reached storage; recovery must redo it.
        frm = self._reopen(context)
        frm.set_dependencies(sm, DemoConcurrencyControl())
        result = frm.recover()
        self._require_recovery("recover()", result.message)
        context.notes.append("Reopen WAL after a simulated crash and redo T1")
        return {"ok": result.ok, "restored": sm.rows.get(rid),
                "last_tx_id_valid": result.last_tx_id >= 1}

    def _multi_tx_recovery(self, context: FRMCaseContext) -> dict:
        frm = context.frm
        sm = DemoStorageManager()
        committed_insert = RID("student", 0, 1)
        uncommitted_insert = RID("student", 0, 2)
        committed_update = RID("student", 0, 3)
        sm.rows = {uncommitted_insert: [2], committed_update: [3, "old"]}

        self._write(frm, LogKind.BEGIN, 1)
        self._write(frm, LogKind.DATA, 1,
                    self._change(committed_insert, ChangeOp.INSERT, NO_RECORD, [1]))
        self._write(frm, LogKind.COMMIT, 1)
        self._write(frm, LogKind.BEGIN, 2)
        self._write(frm, LogKind.DATA, 2,
                    self._change(uncommitted_insert, ChangeOp.INSERT, NO_RECORD, [2]))
        self._write(frm, LogKind.BEGIN, 3)
        self._write(frm, LogKind.DATA, 3,
                    self._change(committed_update, ChangeOp.UPDATE, [3, "old"], [3, "new"]))
        last_lsn = self._write(frm, LogKind.COMMIT, 3)
        _check(frm.flush_log(last_lsn), "flush before simulated crash failed")
        # T1 and T3 need redo; T2 reached storage but must be undone.
        frm = self._reopen(context)
        frm.set_dependencies(sm, DemoConcurrencyControl())
        result = frm.recover()
        self._require_recovery("recover()", result.message)
        context.notes.append("Redo committed T1/T3 and undo uncommitted T2")
        return {
            "ok": result.ok,
            "committed_insert": sm.rows.get(committed_insert),
            "uncommitted_insert": sm.rows.get(uncommitted_insert),
            "committed_update": sm.rows.get(committed_update),
            "last_tx_id_valid": result.last_tx_id >= 3,
        }
