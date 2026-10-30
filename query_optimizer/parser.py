"""QO-02, QO-03: Mengenali Query SELECT dan Penggabungan Tabel.

PIC: QO1 (Angelina / ketua group)
Status: STUB — menunggu implementasi.
"""

from __future__ import annotations

from common.plan import ParsedQuery
from query_optimizer.expression_parser import ExpressionParser
from query_optimizer.lexer import TokenStream
from query_optimizer.statement_parser import StatementParser


class SelectParser:
    """Parser untuk query SELECT termasuk FROM, WHERE, ORDER BY, JOIN.

    TODO (QO1): Implementasikan parser SELECT lengkap.
    """

    def __init__(self, expr_parser: ExpressionParser) -> None:
        self._expr_parser = expr_parser

    def parse_select(self, tokens: TokenStream, sql: str) -> ParsedQuery:
        """Parse SELECT [cols] FROM [tables] [WHERE cond] [ORDER BY col [ASC|DESC]]."""
        raise NotImplementedError("SelectParser.parse_select belum diimplementasi (QO-02)")


class ParserDispatcher:
    """Dispatcher yang mengarahkan token stream ke parser yang sesuai.

    TODO (QO1): Implementasikan dispatch berdasarkan keyword pertama.
    """

    def __init__(self, select_parser: SelectParser, stmt_parser: StatementParser) -> None:
        self._select_parser = select_parser
        self._stmt_parser = stmt_parser

    def parse(self, tokens: TokenStream, sql: str) -> ParsedQuery:
        """Dispatch ke parser yang sesuai berdasarkan keyword pertama.

        Raise QuerySyntaxError jika keyword tidak dikenali.
        """
        raise NotImplementedError("ParserDispatcher.parse belum diimplementasi (QO-02)")

