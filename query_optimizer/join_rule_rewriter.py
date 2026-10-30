"""Purpose: rewrite joins. Mock: do nothing."""

from __future__ import annotations

from typing import Any


def rewrite_join(query: Any | None) -> Any | None:
    """Purpose: turn cartesian into join. Mock: leave query unchanged."""
    return query
