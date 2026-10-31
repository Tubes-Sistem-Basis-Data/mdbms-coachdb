"""Orkestrasi crash recovery."""

from __future__ import annotations

from failure_recovery.ports import (
    CheckpointInterface,
    CheckpointQueryInterface,
    LogFlusher,
    RecoveryInterface,
)
from failure_recovery.recovery.analysis import AnalysisResult, RecoveryReport
from failure_recovery.recovery.analyzer import RecoveryAnalyzer
from failure_recovery.recovery.redo_executor import RedoExecutor
from failure_recovery.recovery.undo_coordinator import UndoCoordinator


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
        return None

    def is_ready(self) -> bool:
        return False

    def _finalize(self, analysis: AnalysisResult) -> None:
        return None
