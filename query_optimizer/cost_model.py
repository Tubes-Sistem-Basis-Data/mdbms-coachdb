"""Estimasi biaya plan dalam jumlah transfer blok."""

from __future__ import annotations

from common.interfaces import StorageManager
from common.plan import PlanNode


class CostModel:
    """Model biaya query plan berdasarkan statistik SM (QO-08)."""

    def __init__(self, catalog: StorageManager | None = None) -> None:
        self._catalog = catalog

    def get_cost(self, plan: PlanNode) -> float:
        """Biaya total plan dalam transfer blok. Dipanggil facade di get_cost."""
        return 0.0

    def estimate(self, plan: PlanNode) -> float:
        """Estimasi biaya satu node plan berdasarkan TableStats."""
        return 0.0


def estimate_cost(plan: PlanNode) -> float:
    """Hitung biaya satu plan."""
    return 0.0
