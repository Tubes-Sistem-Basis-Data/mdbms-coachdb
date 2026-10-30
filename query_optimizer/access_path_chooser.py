"""Purpose: choose access path. Mock: always use sequential scan."""

from __future__ import annotations

from typing import Any


def choose_access_path(plan: Any | None) -> str:
    """Purpose: decide scan/index strategy. Mock: sequential scan."""
    return "SEQUENTIAL_SCAN"
