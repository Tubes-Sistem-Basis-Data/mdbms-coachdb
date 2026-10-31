from common.plan import ParsedQuery
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from query_optimizer.lexer import TokenStream


class TxnIndexParser:
    def parse_transaction(self, tokens: "TokenStream") -> ParsedQuery:
        """Mengenali BEGIN [TRANSACTION], COMMIT, dan ABORT"""
        raise NotImplementedError

    def parse_create_index(self, tokens: "TokenStream") -> ParsedQuery:
        """Mengenali CREATE INDEX <nama> ON <tabel>(<kolom>) [USING BTREE|HASH]"""
        raise NotImplementedError