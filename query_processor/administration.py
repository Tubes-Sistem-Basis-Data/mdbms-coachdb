from __future__ import annotations

from contextlib import contextmanager
from threading import Lock, get_ident
from typing import Iterator

from query_processor.errors import ExecutionError
from query_processor.session import SessionManager


class AdministrationGate:
    """Pisahkan perubahan schema dari seluruh request dan transaksi QP."""

    def __init__(self, sessions: SessionManager) -> None:
        self._sessions = sessions
        self._lock = Lock()
        self._requests: dict[int, int] = {}
        self._exclusive = False

    @contextmanager
    def request(self) -> Iterator[None]:
        owner = get_ident()
        with self._lock:
            if self._exclusive:
                raise ExecutionError("Operasi administrasi sedang berjalan; ulangi permintaan setelah selesai")
            self._requests[owner] = self._requests.get(owner, 0) + 1
        try:
            yield
        finally:
            with self._lock:
                self._requests[owner] -= 1
                if not self._requests[owner]:
                    del self._requests[owner]

    @contextmanager
    def exclusive(self) -> Iterator[None]:
        owner = get_ident()
        with self._lock:
            own_requests = self._requests.get(owner, 0)
            if self._exclusive or own_requests > 1 or sum(self._requests.values()) != own_requests:
                raise ExecutionError("Operasi administrasi ditolak: masih ada request lain yang berjalan")
            if any(session.tx_id is not None or session.in_explicit_tx
                   for session in self._sessions.active_sessions()):
                raise ExecutionError("Operasi administrasi ditolak: masih ada transaksi aktif pada sesi lain")
            self._exclusive = True
        try:
            yield
        finally:
            with self._lock:
                self._exclusive = False
