"""QO-08: Mengestimasi Biaya Query Plan.

PIC: QO3 (Anindya)
Status: STUB — menunggu implementasi.
"""

from __future__ import annotations

from dataclasses import dataclass

from common.interfaces import StorageManager
from common.plan import PlanNode


@dataclass
class Estimate:
    """Hasil estimasi kardinalitas dan biaya suatu plan node."""

    rows: float
    cost: float


class CostModel:
    """Mengestimasi biaya plan dalam satuan transfer blok.

    Menggunakan statistik tabel dari SM (n_r, b_r, l_r, f_r, V(A,r))
    dan rumus cost textbook basis data.

    Aturan: jika n_r=0 atau distinct=0, QO wajib menganggapnya 1
    untuk mencegah pembagian dengan nol.

    TODO (QO3): Implementasikan estimasi biaya untuk setiap jenis node.
    """

    def __init__(self, sm: StorageManager) -> None:
        self._sm = sm

    def get_cost(self, plan: PlanNode) -> float:
        """Hitung biaya akumulasi plan tree dalam satuan transfer blok.

        Biaya = biaya node sendiri + jumlah biaya children.
        """
        # STUB: menunggu implementasi QO3
        return 0.0

    def estimate(self, plan: PlanNode) -> Estimate:
        """Estimasi jumlah baris dan biaya suatu plan node.

        Digunakan oleh Planner dan AccessPathChooser untuk
        membandingkan alternatif plan.
        """
        # STUB: menunggu implementasi QO3
        return Estimate(rows=0.0, cost=0.0)

