"""QO-05, QO-06: Mengenali Query Perubahan Data, Perintah Transaksi dan Indeks.

PIC: QO2 (Arina)
Status: STUB — menunggu implementasi.
"""

from __future__ import annotations

from common.plan import ParsedQuery
from query_optimizer.expression_parser import ExpressionParser
from query_optimizer.lexer import TokenStream


class StatementParser:
    """Parser untuk INSERT, UPDATE, DELETE, CREATE INDEX, dan transaksi.

    TODO (QO2): Implementasikan parser untuk setiap jenis statement.
    """

    def __init__(self, expr_parser: ExpressionParser) -> None:
        self._expr_parser = expr_parser

    def parse_update(self, tokens: TokenStream, sql: str) -> ParsedQuery:
        """Parse UPDATE t SET col = expr [WHERE cond]."""
        raise NotImplementedError("StatementParser.parse_update belum diimplementasi (QO-05)")

    def parse_insert(self, tokens: TokenStream, sql: str) -> ParsedQuery:
        """Parse INSERT INTO t VALUES (v1, v2, ...)."""
        raise NotImplementedError("StatementParser.parse_insert belum diimplementasi (QO-05)")

    def parse_delete(self, tokens: TokenStream, sql: str) -> ParsedQuery:
        """Parse DELETE FROM t [WHERE cond]."""
        raise NotImplementedError("StatementParser.parse_delete belum diimplementasi (QO-05)")

    def parse_create_index(self, tokens: TokenStream, sql: str) -> ParsedQuery:
        """Parse CREATE INDEX name ON t(col) [USING BTREE|HASH]."""
        raise NotImplementedError("StatementParser.parse_create_index belum diimplementasi (QO-06)")

    def parse_transaction(self, tokens: TokenStream, sql: str) -> ParsedQuery:
        """Parse BEGIN [TRANSACTION], COMMIT, ABORT."""
        raise NotImplementedError("StatementParser.parse_transaction belum diimplementasi (QO-06)")

