"""Tampilan query plan untuk EXPLAIN."""

from __future__ import annotations

from typing import Any


def render_plan(plan: Any | None) -> str:
    """Ubah plan jadi teks yang bisa dibaca user (QO-15)."""
    return "mock plan"
