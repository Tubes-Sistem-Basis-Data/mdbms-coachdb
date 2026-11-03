"""Error internal Query Processor."""

from common.errors import MdbmsError


class PlanError(MdbmsError):
    """Plan tidak valid atau tidak didukung."""


class ExecutionError(MdbmsError):
    """Evaluasi ekspresi gagal saat runtime."""
