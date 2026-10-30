"""Stub tokenisasi untuk QO-01.

Tujuan asli: memecah SQL menjadi token agar parser bisa bekerja. Versi mock
hanya mengembalikan token dasar tanpa analisis sintaks yang kompleks.
"""

from __future__ import annotations


def tokenize(sql: str) -> list[str]:
    """Tokenisasi SQL menjadi daftar kata.

    Fungsinya yang sebenarnya adalah membaca input SQL, memisahkan token seperti
    SELECT, FROM, WHERE, koma, dan tanda baca. Untuk milestone 1 kami hanya
    mengembalikan kata-kata dasar dan membiarkan parser utama menangani validasi
    yang lebih lanjut.
    """
    cleaned = sql.strip().rstrip(";")
    if not cleaned:
        return []
    return [token for token in cleaned.replace("(", " ").replace(")", " ").split() if token]
