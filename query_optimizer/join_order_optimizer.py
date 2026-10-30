"""Purpose: order joins. Mock: keep original order."""

from __future__ import annotations

from typing import Any


def order_joins(joins: list[Any] | None) -> list[Any]:
    """Purpose: choose join order. Mock: just return the same list."""
    if joins is None:
        return []
    return list(joins)
