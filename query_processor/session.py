from __future__ import annotations

import threading

from common.interfaces import Session

__all__ = ["Session", "SessionManager"]


class SessionManager:
    def __init__(self) -> None:
        self._next_id: int = 1
        self._sessions: dict[int, Session] = {}
        self._lock = threading.Lock()

    def open_session(self) -> Session:
        raise NotImplementedError("QP1: SessionManager.open_session belum diimplementasikan")

    def get_session(self, session_id: int) -> Session | None:
        raise NotImplementedError("QP1: SessionManager.get_session belum diimplementasikan")

    def close_session(self, session_id: int) -> Session | None:
        raise NotImplementedError("QP1: SessionManager.close_session belum diimplementasikan")

    def active_sessions(self) -> list[Session]:
        raise NotImplementedError("QP1: SessionManager.active_sessions belum diimplementasikan")
