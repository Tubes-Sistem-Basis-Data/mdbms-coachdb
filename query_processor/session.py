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
        return Session(session_id=0)

    def get_session(self, session_id: int) -> Session | None:
        return None

    def close_session(self, session_id: int) -> Session | None:
        return None

    def active_sessions(self) -> list[Session]:
        return []
