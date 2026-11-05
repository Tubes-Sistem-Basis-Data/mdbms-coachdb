from __future__ import annotations

from typing import TYPE_CHECKING

from common.catalog import IndexInfo, IndexKind
from common.expression import Literal
from common.plan import (
    CreateIndex, Delete, Filter, Insert, ParsedQuery, PlanNode, QueryKind,
    TableScan, TransactionCommand, Update,
)
from query_optimizer.expression_parser import ExpressionParser
from query_optimizer.lexer import TokenType

if TYPE_CHECKING:
    from query_optimizer.lexer import TokenStream

T = TokenType


class StatementParser:
    """Parser untuk perintah selain SELECT: UPDATE, INSERT, DELETE, CREATE INDEX, dan transaksi.

    Setiap metode dipanggil saat token sekarang adalah keyword pembuka perintah,
    mengembalikan ParsedQuery dengan sql="", dan berhenti sebelum ';' atau EOF.
    Pengecekan ';' dan EOF dilakukan ParserDispatcher.
    """

    def __init__(self, expr_parser: ExpressionParser | None = None) -> None:
        self._expr = expr_parser if expr_parser is not None else ExpressionParser()

    def parse_update(self, tokens: "TokenStream") -> ParsedQuery:
        """Mengenali UPDATE <tabel> SET <kolom> = <ekspresi> [WHERE <kondisi>]"""
        tokens.expect(T.KEYWORD, "UPDATE")
        table = tokens.expect(T.IDENT).value
        tokens.expect(T.KEYWORD, "SET")
        column = tokens.expect(T.IDENT).value
        tokens.expect(T.OP, "=")
        expression = self._expr.parse_expression(tokens)
        if tokens.match(T.COMMA):
            tokens.error("UPDATE hanya mendukung satu kolom pada SET")
        source = self._parse_target(tokens, table)
        plan = Update(table=table, column=column, expression=expression, children=[source])
        return ParsedQuery(sql="", kind=QueryKind.UPDATE, plan=plan)

    def parse_insert(self, tokens: "TokenStream") -> ParsedQuery:
        """Mengenali INSERT INTO <tabel> VALUES (...) untuk satu baris"""
        tokens.expect(T.KEYWORD, "INSERT")
        tokens.expect(T.KEYWORD, "INTO")
        table = tokens.expect(T.IDENT).value
        tokens.expect(T.KEYWORD, "VALUES")
        tokens.expect(T.LPAREN, "(")
        if tokens.match(T.RPAREN):
            tokens.error("daftar nilai kosong")
        values = [self._parse_value(tokens)]
        while tokens.match(T.COMMA):
            tokens.advance()
            values.append(self._parse_value(tokens))
        tokens.expect(T.RPAREN, ")")
        plan = Insert(table=table, values=values)
        return ParsedQuery(sql="", kind=QueryKind.INSERT, plan=plan)

    def parse_delete(self, tokens: "TokenStream") -> ParsedQuery:
        """Mengenali DELETE FROM <tabel> [WHERE <kondisi>]"""
        tokens.expect(T.KEYWORD, "DELETE")
        tokens.expect(T.KEYWORD, "FROM")
        table = tokens.expect(T.IDENT).value
        source = self._parse_target(tokens, table)
        plan = Delete(table=table, children=[source])
        return ParsedQuery(sql="", kind=QueryKind.DELETE, plan=plan)

    def parse_create_index(self, tokens: "TokenStream") -> ParsedQuery:
        """Mengenali CREATE INDEX <nama> ON <tabel>(<kolom>) [USING BTREE|HASH]"""
        return ParsedQuery(sql="", kind=QueryKind.CREATE_INDEX)

    def parse_transaction(self, tokens: "TokenStream") -> ParsedQuery:
        """Mengenali BEGIN [TRANSACTION], COMMIT, dan ABORT"""
        return ParsedQuery(sql="", kind=QueryKind.TRANSACTION)

    def _parse_target(self, tokens: "TokenStream", table: str) -> PlanNode:
        """Sumber baris UPDATE/DELETE: TableScan, dibungkus Filter jika ada WHERE."""
        node: PlanNode = TableScan(table=table)
        if tokens.match(T.KEYWORD, "WHERE"):
            tokens.advance()
            condition = self._expr.parse_condition(tokens)
            node = Filter(condition=condition, children=[node])
        return node

    def _parse_value(self, tokens: "TokenStream") -> Literal:
        """Satu nilai INSERT. Harus konstanta (angka negatif sudah dilipat jadi Literal)."""
        tok = tokens.peek()
        value = self._expr.parse_expression(tokens)
        if not isinstance(value, Literal):
            tokens.error("nilai INSERT harus berupa konstanta", tok)
        return value