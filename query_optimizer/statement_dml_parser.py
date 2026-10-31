from common.plan import ParsedQuery
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from query_optimizer.lexer import TokenStream


class DMLParser:
    def parse_update(self, tokens: "TokenStream") -> ParsedQuery:
        """Mengenali UPDATE <tabel> SET <kolom> = <ekspresi> [WHERE <kondisi>]"""
        raise NotImplementedError

    def parse_insert(self, tokens: "TokenStream") -> ParsedQuery:
        """Mengenali INSERT INTO <tabel> VALUES (...) untuk satu baris"""
        raise NotImplementedError

    def parse_delete(self, tokens: "TokenStream") -> ParsedQuery:
        """Mengenali DELETE FROM <tabel> [WHERE <kondisi>]"""
        raise NotImplementedError