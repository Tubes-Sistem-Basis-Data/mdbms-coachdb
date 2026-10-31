"""Fase redo saat recovery."""

from __future__ import annotations

from failure_recovery.recovery.analysis import AnalysisResult


class RedoExecutor:
    """Menerapkan ulang after-image transaksi yang sudah commit."""

    def __init__(self, sm) -> None:
        self._sm = sm

    def run(self, analysis: AnalysisResult) -> int:
        return 0
