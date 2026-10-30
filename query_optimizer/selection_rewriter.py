"""Purpose: rewrite WHERE. Mock: do nothing."""

from __future__ import annotations

from typing import Any


def rewrite_selection(condition: Any | None) -> Any | None:
    """Purpose: move predicates. Mock: return as-is."""
    return condition
