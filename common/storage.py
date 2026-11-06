"""Identitas dan isi baris di storage."""

from __future__ import annotations

from dataclasses import dataclass

Value = int | float | str | None
"""Nilai satu kolom: INTEGER int, FLOAT float, CHAR/VARCHAR str, NULL None."""


@dataclass(frozen=True)
class RID:
    """Record id: (tabel, page, slot). Hashable, dipakai sebagai kunci lock dan index."""

    table: str
    page_id: int
    slot: int


@dataclass
class Row:
    """Satu baris hasil baca SM beserta RID-nya."""

    rid: RID
    values: list
