"""Penukaran urutan dan pengelompokan join (QO-12).

M2 – Commutativity : Join(A, B)       ↔ Join(B, A)
M3 – Associativity : Join(Join(A,B),C) ↔ Join(A, Join(B,C))

Strategi: bottom-up greedy.  Setiap node Join dievaluasi dengan semua
transformasi yang sah; kandidat dengan biaya CostModel terendah dipilih,
lalu proses diulang hingga tidak ada perbaikan (fixpoint).
"""

from __future__ import annotations

import copy
from dataclasses import replace

from common.plan import Join, JoinKind, PlanNode
from .cost_model import CostModel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_join(node: PlanNode) -> bool:
    return isinstance(node, Join) and node.kind in (JoinKind.THETA, JoinKind.CROSS)


def _clone_join(node: Join, left: PlanNode, right: PlanNode) -> Join:
    """Buat Join baru dengan anak berbeda, pertahankan kind/condition/algorithm."""
    return Join(
        kind=node.kind,
        condition=node.condition,
        algorithm=node.algorithm,
        children=[left, right],
    )


def _safe_cost(node: PlanNode, model: CostModel) -> float:
    """Hitung biaya; kembalikan inf jika gagal (mis. kolom tidak ditemukan)."""
    try:
        return model.get_cost(node)
    except Exception:
        return float("inf")


# ---------------------------------------------------------------------------
# Transformasi tunggal
# ---------------------------------------------------------------------------

def _commute(node: Join) -> Join:
    """M2 – tukar left dan right child."""
    left, right = node.children
    return _clone_join(node, right, left)


def _rotate_left(node: Join) -> Join | None:
    """M3 – right rotation: Join(Join(A,B),C) → Join(A,Join(B,C)).

    Operand-operand natural join tidak dipindahkan karena semantiknya
    bergantung pada nama kolom yang diwarisi dari kedua sisi.
    """
    # node = Join(left=Join(A,B), right=C)
    left = node.children[0]
    if not _is_join(left):
        return None
    a, b = left.children
    c = node.children[1]
    # outer baru: Join(A, Join(B,C))
    inner = _clone_join(node, b, c)   # pakai kondisi outer untuk inner baru
    # outer anak kiri = a, anak kanan = inner
    # Kondisi outer mungkin tidak valid untuk susunan baru; _safe_cost akan menangkap.
    return _clone_join(left, a, inner)


def _rotate_right(node: Join) -> Join | None:
    """M3 – left rotation: Join(A,Join(B,C)) → Join(Join(A,B),C)."""
    right = node.children[1]
    if not _is_join(right):
        return None
    b, c = right.children
    a = node.children[0]
    inner = _clone_join(node, a, b)
    return _clone_join(right, inner, c)


# ---------------------------------------------------------------------------
# Optimizer utama
# ---------------------------------------------------------------------------

class JoinOrderOptimizer:
    """Menukar urutan dan pengelompokan join (QO-12).

    Parameters
    ----------
    max_passes:
        Batas atas iterasi fixpoint per sub-tree.  Nilai kecil cukup untuk
        query normal; naikkan untuk banyak join.
    """

    def __init__(self, max_passes: int = 8) -> None:
        self._max_passes = max_passes

    # ------------------------------------------------------------------
    # API publik
    # ------------------------------------------------------------------

    def reorder(self, plan: PlanNode, cost_model: CostModel) -> PlanNode:
        """Optimasi ulang urutan join pada *plan* menggunakan *cost_model*.

        Transformasi diterapkan bottom-up secara rekursif, lalu fixpoint
        loop pada setiap Join node sampai tidak ada perbaikan biaya.
        """
        return self._reorder_recursive(plan, cost_model)

    # ------------------------------------------------------------------
    # Implementasi rekursif
    # ------------------------------------------------------------------

    def _reorder_recursive(self, node: PlanNode, model: CostModel) -> PlanNode:
        # Optimalkan semua anak terlebih dahulu (bottom-up)
        node.children = [self._reorder_recursive(child, model) for child in node.children]

        if not _is_join(node):
            return node

        assert isinstance(node, Join)
        return self._optimize_join(node, model)

    def _optimize_join(self, node: Join, model: CostModel) -> Join:
        """Cari Join terbaik dengan commutativity + associativity (fixpoint)."""
        best = node
        best_cost = _safe_cost(best, model)

        for _ in range(self._max_passes):
            improved = False
            candidates = self._generate_candidates(best)
            for candidate in candidates:
                c = _safe_cost(candidate, model)
                if c < best_cost - 1e-9:   # toleransi floating point
                    best = candidate
                    best_cost = c
                    improved = True
            if not improved:
                break

        return best

    def _generate_candidates(self, node: Join) -> list[Join]:
        """Hasilkan semua transformasi M2/M3 yang sah dari *node*."""
        candidates: list[Join] = []

        # M2 – commutativity
        candidates.append(_commute(node))

        # M3 – associativity (dua arah rotasi)
        rot_left = _rotate_left(node)
        if rot_left is not None:
            candidates.append(rot_left)
            # juga commute hasil rotasi
            candidates.append(_commute(rot_left))

        rot_right = _rotate_right(node)
        if rot_right is not None:
            candidates.append(rot_right)
            candidates.append(_commute(rot_right))

        return candidates
