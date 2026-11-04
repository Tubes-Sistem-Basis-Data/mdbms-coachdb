"""Manajemen sesi client Query Processor (QP1)."""

from __future__ import annotations

import threading

from common.interfaces import Session

__all__ = ["Session", "SessionManager"]


class SessionManager:
    """Mengelola siklus hidup Session aktif secara thread-safe."""

    def __init__(self) -> None:
        self._next_id: int = 1
        self._sessions: dict[int, Session] = {}
        self._lock = threading.Lock()

    def open_session(self) -> Session:
        """Buat dan daftarkan sesi baru dengan session_id unik."""
        with self._lock:
            session_id = self._next_id
            self._next_id += 1
            session = Session(session_id=session_id, tx_id=None, in_explicit_tx=False)
            self._sessions[session_id] = session
            return session

    def get_session(self, session_id: int) -> Session | None:
        """Ambil sesi aktif berdasarkan session_id, atau None bila tidak ditemukan."""
        with self._lock:
            return self._sessions.get(session_id)

    def register_session(self, session: Session) -> Session:
        """Pastikan objek Session terdaftar di tabel sesi aktif."""
        with self._lock:
            existing = self._sessions.get(session.session_id)
            if existing is not None:
                return existing
            if session.session_id >= self._next_id:
                self._next_id = session.session_id + 1
            self._sessions[session.session_id] = session
            return session

    def close_session(self, session_id: int) -> Session | None:
        """Hapus sesi dari daftar aktif dan kembalikan objeknya bila ada."""
        with self._lock:
            return self._sessions.pop(session_id, None)

    def active_sessions(self) -> list[Session]:
        """Kembalikan salinan daftar seluruh sesi yang masih aktif."""
        with self._lock:
            return list(self._sessions.values())
