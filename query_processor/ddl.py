from __future__ import annotations

import logging
from threading import RLock
from typing import Callable

from common.errors import MdbmsError
from common.interfaces import Session
from common.result import ExecutionResult
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
    ) -> None:
        self._transactions = transactions
        self._allow_ddl = allow_ddl if enabled is None else bool(enabled)
        self._policy = policy
        self._formatter = formatter or ResultFormatter()
        self._admin_lock = admin_lock or RLock()

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
                result = work()
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
