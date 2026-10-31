"""Fase analisis log saat recovery."""

from __future__ import annotations

from failure_recovery.ports import LogReadInterface
from failure_recovery.recovery.analysis import AnalysisResult


class RecoveryAnalyzer:
    """Membangun peta status transaksi dan daftar perubahan dari log."""

    def __init__(self, log_reader: LogReadInterface) -> None:
        self._log_reader = log_reader

    def analyze(self, start_lsn: int, seed_active) -> AnalysisResult | None:
        return None
