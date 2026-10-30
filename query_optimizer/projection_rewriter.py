"""Purpose: rewrite projection. Mock: keep columns unchanged."""

from __future__ import annotations

from typing import Any


def rewrite_projection(columns: list[Any] | None) -> list[Any]:
    """Purpose: trim columns. Mock: return input as-is."""
    if columns is None:
        return []
    return list(columns)
