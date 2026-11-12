from typing import TYPE_CHECKING

from common.plan import ParsedQuery, QueryKind
from query_optimizer.expression_parser import ExpressionParser
from query_optimizer.lexer import TokenType
from query_optimizer.select_parser import SelectParser
from query_optimizer.statement_parser import StatementParser

if TYPE_CHECKING:
    from query_optimizer.lexer import TokenStream

T = TokenType


class ParserDispatcher:
    """Memilih parser berdasarkan keyword pertama: SELECT ke SelectParser, sisanya ke StatementParser."""

    def __init__(self) -> None:
        self._expr_parser = ExpressionParser()
        self._select_parser = SelectParser(self._expr_parser)
        self._statement_parser = StatementParser(self._expr_parser)
        self._expr_parser.set_subquery_parser(self._select_parser.parse_select)

    def parse(self, tokens: "TokenStream", sql: str) -> ParsedQuery:
        """Parse aliran token menjadi ParsedQuery. Dipanggil facade QO di parse_query."""
        # ';' dan EOF hanya dicek di sini, sub-parser cukup berhenti di akhir grammar-nya.
        # Sub-parser mengembalikan sql="", dispatcher yang mengisi sql lengkap.
        tok = tokens.peek()
        if tok.type == T.EOF:
            tokens.error("query kosong")
        if tok.type != T.KEYWORD:
            tokens.error("pernyataan tidak dikenal")

        match tok.value:
            case "SELECT":
                parsed = self._select_parser.parse_select(tokens)
            case "UPDATE":
                parsed = self._statement_parser.parse_update(tokens)
            case "INSERT":
                parsed = self._statement_parser.parse_insert(tokens)
            case "DELETE":
                parsed = self._statement_parser.parse_delete(tokens)
            case "CREATE":
                if tokens.peek(1).type == T.KEYWORD and tokens.peek(1).value == "TABLE":
                    parsed = self._statement_parser.parse_create_table(tokens)
                else:
                    parsed = self._statement_parser.parse_create_index(tokens)
            case "DROP":
                parsed = self._statement_parser.parse_drop_table(tokens)
            case "BEGIN" | "COMMIT" | "ABORT":
                parsed = self._statement_parser.parse_transaction(tokens)
            case "EXPLAIN":
                tokens.advance()
                if tokens.match(T.KEYWORD, "EXPLAIN"):
                    tokens.error("EXPLAIN tidak boleh bertumpuk")
                parsed = self.parse(tokens, sql)
                parsed.kind = QueryKind.EXPLAIN
                return parsed
            case _:
                tokens.error("pernyataan tidak dikenal")

        if tokens.match(T.SEMICOLON):
            tokens.advance()
        if not tokens.is_at_end():
            tokens.error("token tidak terduga setelah akhir query")
        parsed.sql = sql
        return parsed
