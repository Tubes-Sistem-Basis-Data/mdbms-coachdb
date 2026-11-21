from __future__ import annotations

import logging
from contextlib import nullcontext
from threading import RLock
from typing import Callable

from common.errors import LogError, MdbmsError
from common.interfaces import Session
from common.log import CheckpointResult
from common.result import ExecutionResult
from query_processor.administration import AdministrationGate
from query_processor.result import ResultFormatter
from query_processor.transactions import TransactionCoordinator

logger = logging.getLogger("coachdb.qp")


class DDLCoordinator:
    def __init__(
        self,
        transactions: TransactionCoordinator | None = None,
        *,
        allow_ddl: bool = False,
        enabled: bool | None = None,
        policy: Callable[[], bool] | None = None,
        formatter: ResultFormatter | None = None,
        admin_lock: RLock | None = None,
        gate: AdministrationGate | None = None,
        checkpoint: Callable[[], CheckpointResult] | None = None,
    ) -> None:
        self._transactions = transactions
        self._allow_ddl = allow_ddl if enabled is None else bool(enabled)
        self._policy = policy
        self._formatter = formatter or ResultFormatter()
        self._admin_lock = admin_lock or RLock()
        self._gate = gate
        self._checkpoint = checkpoint

    @property
    def admin_lock(self) -> RLock:
        return self._admin_lock

    def is_enabled(self) -> bool:
        if self._policy is not None:
            return bool(self._policy())
        return self._allow_ddl

    def run(
        self,
        session: Session,
        work: Callable[[], ExecutionResult],
    ) -> ExecutionResult:
        if session is None:
            return ExecutionResult(
                ok=False,
                message="ERROR [TX]: Sesi tidak valid untuk operasi DDL",
            )

        if self._transactions is not None:
            blocked = self._transactions.blocked_result(session)
            if blocked is not None:
                return blocked

        if session.in_explicit_tx or session.tx_id is not None:
            return ExecutionResult(
                ok=False,
                message="ERROR [TX]: Operasi DDL tidak diizinkan di dalam transaksi aktif",
            )

        with self._admin_lock:
            if self._transactions is not None:
                blocked = self._transactions.blocked_result(session)
                if blocked is not None:
                    return blocked

            if session.in_explicit_tx or session.tx_id is not None:
                return ExecutionResult(
                    ok=False,
                    message="ERROR [TX]: Operasi DDL tidak diizinkan di dalam transaksi aktif",
                )

            if not self.is_enabled():
                return ExecutionResult(
                    ok=False,
                    message=(
                        "ERROR [DDL]: Eksekusi DDL belum diaktifkan karena kebijakan "
                        "integrasi storage dan recovery belum terverifikasi"
                    ),
                )

            try:
                guard = self._gate.exclusive() if self._gate is not None else nullcontext()
                with guard:
                    # Semua DATA lama sudah durable dan berada sebelum batas redo ini.
                    self._make_checkpoint("sebelum DDL")
                    try:
                        result = work()
                    except Exception as error:
                        try:
                            self._make_checkpoint("setelah DDL gagal")
                        except Exception as checkpoint_error:
                            raise LogError(f"{error}; {checkpoint_error}") from error
                        raise
                    self._make_checkpoint("setelah DDL")
            except MdbmsError as exc:
                return ExecutionResult(ok=False, message=self._formatter.format_error(exc))
            except Exception as exc:
                logger.exception("Kesalahan internal saat menjalankan operasi DDL")
                return ExecutionResult(ok=False, message=self._formatter.format_error(exc))

            if not isinstance(result, ExecutionResult):
                return ExecutionResult(
                    ok=False,
                    message="ERROR [INTERNAL]: Hasil eksekusi DDL tidak valid",
                )

            if result.ok:
                result.affected_rows = 0
            elif not result.message:
                return ExecutionResult(
                    ok=False,
                    message="ERROR [DDL]: Operasi DDL gagal dijalankan",
                )

            return result

    def _make_checkpoint(self, phase: str) -> None:
        if self._checkpoint is None:
            return
        try:
            result = self._checkpoint()
        except Exception as exc:
            raise LogError(f"Checkpoint {phase} gagal: {exc}") from exc
        if (not isinstance(result, CheckpointResult) or not result.ok
                or type(result.lsn) is not int or result.lsn <= 0):
            if isinstance(result, CheckpointResult):
                message = result.message or "hasil atau LSN tidak valid"
            else:
                message = "hasil tidak valid"
            detail = " Perubahan schema mungkin sudah diterapkan." if phase != "sebelum DDL" else ""
            raise LogError(f"Checkpoint {phase} gagal: {message}.{detail}")
