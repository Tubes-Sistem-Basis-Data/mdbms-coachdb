"""Mengubah cartesian product dengan seleksi menjadi join (QO-11).

Aturan:
  Filter(cond, Cross(A, B))  -->  Theta(cond_join, A, B) + sisa Filter jika ada

Algoritma:
1. Jalan rekursif ke seluruh pohon plan.
2. Jika ditemukan pola Filter → Cross (satu lapisan atau bertumpuk), pisahkan
   predikat kondisi WHERE menjadi dua kelompok:
     - predikat *join*  : predikat yang mereferensikan kolom dari *kedua* sisi.
     - predikat *sisa*  : predikat yang hanya menyentuh satu sisi (atau bukan
                          perbandingan kolom-ke-kolom antar-tabel).
3. Jika ada predikat join, buat node Join(THETA, cond_join) menggantikan Cross.
   Predikat sisa (bila ada) dibungkus kembali sebagai Filter di atasnya.
4. Transformasi dilakukan untuk setiap lapisan Filter × Cross secara bottom-up,
   sehingga query multi-tabel ditangani secara lengkap.
"""

from __future__ import annotations

from common.expression import And, Comparison, CompOp, ColumnRef, Expression
from common.plan import Filter, Join, JoinKind, PlanNode


# ---------------------------------------------------------------------------
# Fungsi bantu
# ---------------------------------------------------------------------------

def _tables_in_expr(expr: Expression) -> set[str]:
    """Kumpulkan semua nama tabel yang dirujuk oleh suatu ekspresi."""
    if isinstance(expr, ColumnRef):
        return {expr.table} if expr.table else set()
    if isinstance(expr, Comparison):
        return _tables_in_expr(expr.left) | _tables_in_expr(expr.right)
    if isinstance(expr, And):
        result: set[str] = set()
        for c in expr.conditions:
            result |= _tables_in_expr(c)
        return result
    return set()


def _source_tables(node: PlanNode) -> set[str]:
    """Nama-nama tabel yang menjadi asal dari node plan (rekursif)."""
    from common.plan import TableScan, IndexScan  # hindari circular import atas
    if isinstance(node, (TableScan, IndexScan)):
        return {node.table}
    result: set[str] = set()
    for child in node.children:
        result |= _source_tables(child)
    return result


def _split_conditions(
    conditions: list[Expression],
    left_tables: set[str],
    right_tables: set[str],
) -> tuple[list[Expression], list[Expression]]:
    """Pisah kondisi menjadi (predikat_join, predikat_sisa).

    *predikat_join*  : menyentuh tabel dari kiri DAN kanan sekaligus.
    *predikat_sisa*  : lainnya (hanya kiri, hanya kanan, atau literal).
    """
    join_preds: list[Expression] = []
    rest_preds: list[Expression] = []
    for cond in conditions:
        tables = _tables_in_expr(cond)
        if tables & left_tables and tables & right_tables:
            join_preds.append(cond)
        else:
            rest_preds.append(cond)
    return join_preds, rest_preds


def _make_condition(preds: list[Expression]) -> Expression:
    """Buat satu ekspresi kondisi dari daftar predikat (AND bila lebih dari satu)."""
    assert preds, "_make_condition dipanggil dengan daftar kosong"
    return preds[0] if len(preds) == 1 else And(conditions=preds)


def _flatten_conditions(expr: Expression) -> list[Expression]:
    """Ratakan And bersarang menjadi daftar predikat datar."""
    if isinstance(expr, And):
        result: list[Expression] = []
        for c in expr.conditions:
            result.extend(_flatten_conditions(c))
        return result
    return [expr]


# ---------------------------------------------------------------------------
# Kelas utama
# ---------------------------------------------------------------------------

class JoinRuleRewriter:
    """Mengubah Filter(Cross(A, B)) menjadi Theta-Join(A, B) (QO-11).

    Transformasi dilakukan bottom-up: anak-anak diproses lebih dulu sebelum
    orang tua, sehingga rantai join/cross multi-tabel ditangani secara lengkap.
    """

    def apply(self, plan: PlanNode) -> PlanNode:
        """Entry-point: rekursif ke seluruh pohon lalu terapkan aturan."""
        # Proses anak-anak terlebih dahulu (bottom-up)
        plan.children = [self.apply(child) for child in plan.children]
        return self._try_rewrite(plan)

    # ------------------------------------------------------------------
    # Inti transformasi
    # ------------------------------------------------------------------

    def _try_rewrite(self, node: PlanNode) -> PlanNode:
        """Jika node adalah Filter di atas Cross Join, coba ubah menjadi Theta Join."""
        if not isinstance(node, Filter):
            return node

        cross = node.children[0]
        if not isinstance(cross, Join) or cross.kind is not JoinKind.CROSS:
            return node

        # Ratakan kondisi AND agar mudah dipartisi
        conditions = _flatten_conditions(node.condition)

        left_tables = _source_tables(cross.children[0])
        right_tables = _source_tables(cross.children[1])

        join_preds, rest_preds = _split_conditions(conditions, left_tables, right_tables)

        if not join_preds:
            # Tidak ada predikat yang menghubungkan kedua sisi → tetap CROSS
            return node

        # Bangun Theta Join menggantikan Cross
        join_cond = _make_condition(join_preds)
        theta = Join(
            kind=JoinKind.THETA,
            condition=join_cond,
            children=cross.children,
            # Warisi metadata estimasi (akan diisi ulang oleh CostModel)
            estimated_rows=cross.estimated_rows,
            estimated_cost=cross.estimated_cost,
        )

        if not rest_preds:
            # Semua predikat jadi kondisi join → tidak perlu Filter sisa
            return theta

        # Masih ada predikat yang tidak menjadi join condition → bungkus Filter
        rest_cond = _make_condition(rest_preds)
        return Filter(condition=rest_cond, children=[theta])

