"""Port internal antar-subkomponen Failure Recovery Manager."""

# ruff: noqa: F401

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from common.log import (
    CheckpointResult,
    LogRecord,
    LogResult,
    RecoveryResult,
    RecoveryTarget,
)
from common.transaction import TxResult

if TYPE_CHECKING:
    from failure_recovery.checkpoint.meta import CheckpointMeta


class LogWriter:
    """Placeholder port penulis log."""


class LogFlusher:
    """Placeholder port flush log."""


class LogReadPort:
    """Placeholder port pembaca log."""


class RollbackPort:
    """Placeholder port rollback."""


class CheckpointPort:
    """Placeholder port checkpoint."""


class CheckpointQueryPort:
    """Placeholder port query checkpoint."""


class RecoveryPort:
    """Placeholder port recovery."""
