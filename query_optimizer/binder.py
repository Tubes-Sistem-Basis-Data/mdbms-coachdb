from __future__ import annotations

from common.interfaces import StorageManager
from common.plan import ParsedQuery


class Binder:
    # Store the catalog connection
    def __init__(self, sm: StorageManager) -> None:
        self._sm = sm

    # Check tables and columns in the query
    def bind(self, query: ParsedQuery) -> ParsedQuery:
        return query
