"""Rollback transaksi lewat Storage Manager."""

# ruff: noqa: F401

from __future__ import annotations

from common.interfaces.storage_manager import StorageManager
from common.transaction import TxResult
from failure_recovery.ports import LogReadPort, RollbackPort


class RollbackManager:
    """Placeholder rollback manager."""
