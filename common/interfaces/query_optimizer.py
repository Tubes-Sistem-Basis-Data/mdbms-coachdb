"""Facade Query Optimizer."""

from __future__ import annotations

from abc import ABC, abstractmethod

from common.plan import ParsedQuery, PlanNode


class QueryOptimizer(ABC):
    """Parsing, validasi, dan optimasi query. Dipanggil QP, memakai katalog SM."""

    @abstractmethod
    def parse_query(self, sql: str) -> ParsedQuery:
        """Parse dan validasi SQL. Raise QuerySyntaxError atau SemanticError."""

    @abstractmethod
    def optimize_query(self, query: ParsedQuery) -> ParsedQuery:
        """Kembalikan query dengan plan termurah."""

    @abstractmethod
    def get_cost(self, plan: PlanNode) -> float:
        """Estimasi biaya plan dalam satuan transfer blok."""

    @abstractmethod
    def explain(self, query: ParsedQuery) -> str:
        """Teks plan sebelum dan sesudah optimasi beserta biaya. Dipakai perintah EXPLAIN."""
