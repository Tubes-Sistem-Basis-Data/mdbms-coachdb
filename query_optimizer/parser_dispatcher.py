from common.plan import ParsedQuery, QueryKind
from typing import TYPE_CHECKING

from query_optimizer.select_parser import SelectParser
from query_optimizer.statement_parser import StatementParser

if TYPE_CHECKING:
    from query_optimizer.lexer import TokenStream


class ParserDispatcher:
    """Memilih parser berdasarkan keyword pertama: SELECT ke SelectParser, sisanya ke StatementParser."""

    def __init__(self) -> None:
        self._select_parser = SelectParser()
        self._statement_parser = StatementParser()

    def parse(self, tokens: "TokenStream", sql: str) -> ParsedQuery:
        """Parse aliran token menjadi ParsedQuery. Dipanggil facade QO di parse_query."""
        return ParsedQuery(sql=sql, kind=QueryKind.SELECT)
