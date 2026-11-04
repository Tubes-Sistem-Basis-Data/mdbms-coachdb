from common.plan import ParsedQuery, QueryKind
from typing import TYPE_CHECKING

from query_optimizer.expression_parser import ExpressionParser
from query_optimizer.select_parser import SelectParser
from query_optimizer.statement_parser import StatementParser

if TYPE_CHECKING:
    from query_optimizer.lexer import TokenStream


class ParserDispatcher:
    """Memilih parser berdasarkan keyword pertama: SELECT ke SelectParser, sisanya ke StatementParser."""

    def __init__(self) -> None:
        # TODO(QO-02): keputusan: buat satu ExpressionParser di sini lalu suntikkan ke
        # SelectParser(expr) dan StatementParser(expr). QO2 menambahkan
        # __init__(self, expr_parser) di StatementParser.
        self._expr_parser = ExpressionParser()
        self._select_parser = SelectParser(self._expr_parser)
        self._statement_parser = StatementParser()

    def parse(self, tokens: "TokenStream", sql: str) -> ParsedQuery:
        """Parse aliran token menjadi ParsedQuery. Dipanggil facade QO di parse_query."""
        # TODO(QO-02):
        # 1. tok = tokens.peek(). Kalau EOF -> tokens.error("query kosong").
        # 2. Kalau bukan KEYWORD -> tokens.error("pernyataan tidak dikenal").
        # 3. Pilih parser berdasarkan tok.value (match-case):
        #    SELECT            -> self._select_parser.parse_select(tokens)
        #    UPDATE            -> self._statement_parser.parse_update(tokens)
        #    INSERT            -> parse_insert
        #    DELETE            -> parse_delete
        #    CREATE            -> parse_create_index
        #    BEGIN/COMMIT/ABORT-> parse_transaction
        #    EXPLAIN           -> advance, parsed = self.parse(tokens, sql) (rekursif),
        #                         lalu parsed.kind = QueryKind.EXPLAIN, plan tetap plan query di dalamnya
        #    lainnya           -> tokens.error("pernyataan tidak dikenal")
        # 4. Keputusan: ';' dan EOF hanya dicek di sini, bukan di sub-parser.
        #    Setelah parser selesai: konsumsi SEMICOLON jika ada, lalu wajib EOF.
        #    Kalau belum EOF -> tokens.error("token tidak terduga setelah akhir query").
        #    Panggilan rekursif EXPLAIN cukup dicek sekali, jangan dua kali.
        # 5. Keputusan: sub-parser mengembalikan sql="", dispatcher yang mengisi
        #    parsed.sql = sql, lalu return parsed.
        return ParsedQuery(sql=sql, kind=QueryKind.SELECT)
