from __future__ import annotations

from typing import TYPE_CHECKING

from common.catalog import DataType
from common.expression import (
    And, ArithOp, BinaryOp, ColumnRef, Comparison, CompOp, Expression, Literal,
)
from query_optimizer.lexer import TokenType

if TYPE_CHECKING:
    from query_optimizer.lexer import TokenStream


class _DefaultExpression(Expression):
    """Ekspresi kosong, nilai default selama parser belum diimplementasikan."""
    pass


_COMP_OPS: dict[str, CompOp] = {op.value: op for op in CompOp}
_ADD_OPS: dict[str, ArithOp] = {"+": ArithOp.ADD, "-": ArithOp.SUB}
_MUL_OPS: dict[str, ArithOp] = {"*": ArithOp.MUL, "/": ArithOp.DIV}


class ExpressionParser:
    def parse_condition(self, tokens: "TokenStream") -> Expression:
        """Mengenali kondisi WHERE, beberapa kondisi digabung dengan AND"""
        conditions = [self._parse_comparison(tokens)]
        while tokens.match(TokenType.KEYWORD, "AND"):
            tokens.advance()
            conditions.append(self._parse_comparison(tokens))
        if len(conditions) == 1:
            return conditions[0]
        return And(conditions)

    def parse_expression(self, tokens: "TokenStream") -> Expression:
        """Mengenali ekspresi aritmetika dengan operator + dan -"""
        left = self._parse_term(tokens)
        while self._at_op(tokens, _ADD_OPS):
            op = _ADD_OPS[tokens.advance().value]
            right = self._parse_term(tokens)
            left = BinaryOp(op, left, right)
        return left

    def _parse_comparison(self, tokens: "TokenStream") -> Expression:
        """Mengenali perbandingan dua ekspresi dengan = <> > >= < <="""
        left = self.parse_expression(tokens)
        if not self._at_op(tokens, _COMP_OPS):
            tokens.error("diharapkan operator perbandingan (=, <>, >, >=, <, <=)")
        op = _COMP_OPS[tokens.advance().value]
        right = self.parse_expression(tokens)
        return Comparison(op, left, right)

    def _parse_term(self, tokens: "TokenStream") -> Expression:
        """Mengenali suku dengan operator * dan /"""
        left = self._parse_unary(tokens)
        while self._at_op(tokens, _MUL_OPS):
            op = _MUL_OPS[tokens.advance().value]
            right = self._parse_unary(tokens)
            left = BinaryOp(op, left, right)
        return left

    def _parse_unary(self, tokens: "TokenStream") -> Expression:
        """Mengenali tanda minus di depan suatu faktor"""
        if tokens.match(TokenType.OP, "+"):
            tokens.advance()
            return self._parse_unary(tokens)
        if tokens.match(TokenType.OP, "-"):
            tokens.advance()
            operand = self._parse_unary(tokens)
            if isinstance(operand, Literal) and operand.type in (DataType.INTEGER, DataType.FLOAT):
                return Literal(-operand.value, operand.type)
            return BinaryOp(ArithOp.SUB, Literal(0, DataType.INTEGER), operand)
        return self._parse_factor(tokens)

    def _parse_factor(self, tokens: "TokenStream") -> Expression:
        """Mengenali kolom, nilai konstan, atau ekspresi dalam kurung"""
        tok = tokens.peek()
        if tok.type == TokenType.INTEGER:
            tokens.advance()
            return Literal(tok.value, DataType.INTEGER)
        if tok.type == TokenType.FLOAT:
            tokens.advance()
            return Literal(tok.value, DataType.FLOAT)
        if tok.type == TokenType.STRING:
            tokens.advance()
            return Literal(tok.value, DataType.VARCHAR)
        if tok.type == TokenType.IDENT:
            tokens.advance()
            if tokens.match(TokenType.DOT):
                tokens.advance()
                kolom = tokens.expect(TokenType.IDENT)
                return ColumnRef(tok.value, kolom.value)
            return ColumnRef(None, tok.value)
        if tok.type == TokenType.LPAREN:
            tokens.advance()
            inner = self.parse_expression(tokens)
            tokens.expect(TokenType.RPAREN, ")")
            return inner
        tokens.error("diharapkan kolom, nilai konstan, atau '('")

    @staticmethod
    def _at_op(tokens: "TokenStream", ops: dict) -> bool:
        tok = tokens.peek()
        return tok.type == TokenType.OP and tok.value in ops