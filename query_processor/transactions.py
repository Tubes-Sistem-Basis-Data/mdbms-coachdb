from __future__ import annotations

import logging
from typing import Callable

from common.errors import MdbmsError, TransactionAbortedError
from common.interfaces import ConcurrencyControlManager, Session
from common.result import ExecutionResult
from common.transaction import TxStatus
from query_processor.result import ResultFormatter


logger = logging.getLogger("coachdb.qp")


class TransactionCoordinator:
    """Mengelola transaksi dan auto-commit pada sesi"""

    def __init__(self, ccm: ConcurrencyControlManager) -> None:
        self._ccm = ccm

    def begin(self, session: Session) -> ExecutionResult:
        if session.tx_id is not None or session.in_explicit_tx:
            return ExecutionResult(ok=False, message="ERROR [TX]: Transaksi sudah aktif")

        # Minta transaksi baru ke CCM
        try:
            tx_id = self._ccm.begin_transaction()
        except Exception as error:
            return self._error(error)

        session.tx_id = tx_id
        session.in_explicit_tx = True
        return ExecutionResult(ok=True, message=f"BEGIN (tx {tx_id})")

    def commit(self, session: Session) -> ExecutionResult:
        return self._finish(session, True)

    def abort(self, session: Session) -> ExecutionResult:
        return self._finish(session, False)

    def run_autocommit(self, session: Session, work: Callable[[int], ExecutionResult]) -> ExecutionResult:
        if session.tx_id is not None or session.in_explicit_tx:
            return ExecutionResult(
                ok=False,
                message="ERROR [TX]: Auto-commit membutuhkan sesi tanpa transaksi aktif",
            )

        # Mulai transaksi untuk satu query
        try:
            session.tx_id = self._ccm.begin_transaction()
        except Exception as error:
            return self._error(error)

        try:
            result = work(session.tx_id)
        except Exception as error:
            try:
                self.handle_runtime_error(session, error)
            except Exception as cleanup_error:
                result = self._error(error)
                result.message += f"; cleanup gagal: {cleanup_error}"
                return result
            return self._error(error)

        # Batalkan transaksi jika query gagal
        if not result.ok:
            response = self.abort(session)
            if not response.ok:
                return ExecutionResult(
                    ok=False,
                    message=f"{result.message or 'Query gagal'}; {response.message}",
                )
            return result

        # Hasil query dikembalikan setelah commit berhasil
        response = self.commit(session)
        if not response.ok:
            return response
        return result

    def abort_on_disconnect(self, session: Session) -> None:
        if session.tx_id is not None:
            result = self.abort(session)
            if not result.ok:
                raise MdbmsError(result.message)

    def handle_runtime_error(self, session: Session, error: Exception) -> None:
        if isinstance(error, TransactionAbortedError):
            # Transaksi sudah dibatalkan oleh CCM
            if session.tx_id == error.tx_id:
                self._clear(session)
            return

        if session.tx_id is not None:
            result = self.abort(session)
            if not result.ok:
                raise MdbmsError(result.message) from error

    def _finish(self, session: Session, commit: bool) -> ExecutionResult:
        command = "COMMIT" if commit else "ABORT"
        tx_id = session.tx_id
        if tx_id is None:
            return ExecutionResult(
                ok=False,
                message=f"ERROR [TX]: Tidak ada transaksi aktif untuk di-{command}",
            )

        # Minta CCM menyelesaikan transaksi
        try:
            if commit:
                result = self._ccm.commit(tx_id)
            else:
                result = self._ccm.abort(tx_id)
                
        except TransactionAbortedError as error:
            if error.tx_id == tx_id:
                self._clear(session)
                
            return self._error(error)
        
        except Exception as error:
            return self._error(error)

        if result.tx_id != tx_id:
            return ExecutionResult(ok=False, message="ERROR [TX]: CCM mengembalikan tx_id yang berbeda")

        # Pertahankan tx_id jika transaksi belum pasti selesai
        if result.status in (TxStatus.COMMITTED, TxStatus.ABORTED):
            self._clear(session)

        expected = TxStatus.COMMITTED if commit else TxStatus.ABORTED
        ok = result.ok and result.status == expected
        message = result.message
        if not message:
            if ok:
                message = f"{command} (tx {tx_id})"
            else:
                message = f"ERROR [TX]: {command} transaksi {tx_id} gagal (status {result.status.value})"

        return ExecutionResult(ok=ok, message=message)

    @staticmethod
    def _clear(session: Session) -> None:
        session.tx_id = None
        session.in_explicit_tx = False

    @staticmethod
    def _error(error: Exception) -> ExecutionResult:
        if not isinstance(error, MdbmsError):
            logger.error("Kesalahan internal koordinator transaksi", exc_info=True)
        return ExecutionResult(ok=False, message=ResultFormatter().format_error(error))
