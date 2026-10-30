"""Purpose: genetic join optimizer. Mock: return input unchanged."""

from __future__ import annotations

from typing import Any


def optimize_join_order(data: list[Any] | None) -> list[Any]:
    """Purpose: find best join order. Mock: do nothing."""
    if data is None:
        return []
    return list(data)
