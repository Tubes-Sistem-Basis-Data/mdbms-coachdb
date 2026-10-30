"""Penulis Write-Ahead Log."""

# ruff: noqa: F401

from __future__ import annotations

from pathlib import Path

from common.log import LogRecord, LogResult
from failure_recovery.ports import LogFlusher, LogWriter


class WalLogger:
    """Placeholder penulis WAL."""
