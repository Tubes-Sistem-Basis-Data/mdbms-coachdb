"""Identitas dan isi baris di storage."""

from __future__ import annotations

from dataclasses import dataclass


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
