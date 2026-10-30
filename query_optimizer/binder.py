"""QO-07: Memvalidasi Query terhadap Katalog.

PIC: QO3 (Anindya)
Status: STUB — menunggu implementasi.
"""

from __future__ import annotations

from common.interfaces import StorageManager
from common.plan import ParsedQuery


class Binder:
    """Memvalidasi tabel dan kolom dalam ParsedQuery terhadap katalog SM.

    Tugas:
    - Memastikan semua tabel yang direferensikan ada di katalog.
    - Memastikan semua kolom yang direferensikan ada di tabel yang benar.
    - Mengisi field `table` pada ColumnRef yang masih None (resolusi nama).
    - Mendeteksi ambiguitas kolom pada query multi-tabel.

    TODO (QO3): Implementasikan validasi lengkap.
    """

    def __init__(self, sm: StorageManager) -> None:
        self._sm = sm

    def bind(self, query: ParsedQuery) -> ParsedQuery:
        """Validasi dan lengkapi referensi tabel/kolom pada query.

        Raise SemanticError jika tabel atau kolom tidak valid.
        """
        # STUB: menunggu implementasi QO3
        # Sementara langsung kembalikan query tanpa validasi
        return query

