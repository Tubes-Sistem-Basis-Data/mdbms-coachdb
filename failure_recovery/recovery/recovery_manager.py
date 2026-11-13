"""Orkestrasi crash recovery."""

from __future__ import annotations

import logging

from common.errors import MdbmsError, RecoveryError
from failure_recovery.ports import (
    CheckpointInterface,
    CheckpointQueryInterface,
    LogFlusher,
    RecoveryInterface,
)
from failure_recovery.recovery.analysis import (
    AnalysisResult,
    RecoveryReport,
    RecoveryStatus,
    TransactionState,
)
from failure_recovery.recovery.analyzer import RecoveryAnalyzer
from failure_recovery.recovery.redo_executor import RedoExecutor
from failure_recovery.recovery.undo_coordinator import UndoCoordinator

logger = logging.getLogger("coachdb.frm")


class RecoveryManager(RecoveryInterface):
    """Menjalankan analisis, redo, dan undo saat startup."""

    def __init__(
        self,
        analyzer: RecoveryAnalyzer,
        redo: RedoExecutor,
        undo: UndoCoordinator,
        checkpoint_query: CheckpointQueryInterface,
        checkpoint_service: CheckpointInterface,
        log_flusher: LogFlusher,
        sm,
        ccm,
    ) -> None:
        self.analyzer = analyzer
        self.redo = redo
        self.undo = undo
        self._ready = False
        self._checkpoint_query = checkpoint_query
        self._checkpoint_service = checkpoint_service
        self._log_flusher = log_flusher
        self._sm = sm
        self._ccm = ccm

    def recover(self) -> RecoveryReport | None:
        self._ready = False
        start_lsn = 0
        try:
            latest = self._checkpoint_query.find_latest_completed()
            if latest is not None:
                start_lsn = latest.checkpoint_lsn
            seed_active = list(latest.active_tx) if latest is not None else []
            seed_max_tx_id = latest.max_tx_id if latest is not None else 0

            analysis = self.analyzer.analyze(start_lsn, seed_active)
            if analysis is None:
                raise RecoveryError("Analisis log recovery gagal")
            analysis.max_tx_id = max(analysis.max_tx_id, seed_max_tx_id)

            redo_count = self.redo.run(analysis)
            undone_tx, undo_count = self.undo.run(analysis)
            self._log_flusher.flush_log_until(analysis.last_lsn)

            committed_tx = sorted(
                tx_id
                for tx_id, state in analysis.tx_state.items()
                if state is TransactionState.COMMITTED
            )
            aborted_tx = sorted(
                tx_id
                for tx_id, state in analysis.tx_state.items()
                if state is TransactionState.ABORTED
            )
            report = RecoveryReport(
                status=RecoveryStatus.SUCCESS,
                start_lsn=analysis.start_lsn,
                committed_tx=committed_tx,
                aborted_tx=aborted_tx,
                undone_tx=undone_tx,
                redo_count=redo_count,
                undo_count=undo_count,
                max_tx_id=analysis.max_tx_id,
            )
            self._finalize(analysis)
            return report
        except MdbmsError as exc:
            return RecoveryReport(
                status=RecoveryStatus.FAILED,
                start_lsn=start_lsn,
                errors=[str(exc)],
            )
        except Exception as exc:
            return RecoveryReport(
                status=RecoveryStatus.FAILED,
                start_lsn=start_lsn,
                errors=[f"{type(exc).__name__}: {exc}"],
            )

    def is_ready(self) -> bool:
        return self._ready

    def _finalize(self, analysis: AnalysisResult) -> None:
        self._ready = True
        self._rebuild_indexes()
        if self._checkpoint_service is None:
            return
        try:
            result = self._checkpoint_service.checkpoint()
            if result is not None and not result.ok:
                logger.warning("Checkpoint setelah recovery gagal: %s", result.message)
        except Exception as exc:
            logger.warning("Checkpoint setelah recovery gagal: %s", exc)

    def _rebuild_indexes(self) -> None:
        rebuild = getattr(self._sm, "rebuild_indexes", None)
        if not callable(rebuild):
            return
        try:
            rebuild()
        except Exception as exc:
            logger.warning("Rebuild index setelah recovery gagal: %s", exc)
