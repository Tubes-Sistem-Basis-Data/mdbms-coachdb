from common.expression import Expression
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from query_optimizer.lexer import TokenStream


class _DefaultExpression(Expression):
    """Ekspresi kosong, nilai default selama parser belum diimplementasikan."""
    pass


class ExpressionParser:
    def parse_condition(self, tokens: "TokenStream") -> Expression:
        """Mengenali kondisi WHERE, beberapa kondisi digabung dengan AND"""
        return _DefaultExpression()

    def parse_expression(self, tokens: "TokenStream") -> Expression:
        """Mengenali ekspresi aritmetika dengan operator + dan -"""
        return _DefaultExpression()

    def _parse_comparison(self, tokens: "TokenStream") -> Expression:
        """Mengenali perbandingan dua ekspresi dengan = <> > >= < <="""
        return _DefaultExpression()

    def _parse_term(self, tokens: "TokenStream") -> Expression:
        """Mengenali suku dengan operator * dan /"""
        return _DefaultExpression()

    def _parse_unary(self, tokens: "TokenStream") -> Expression:
        """Mengenali tanda minus di depan suatu faktor"""
        return _DefaultExpression()

    def _parse_factor(self, tokens: "TokenStream") -> Expression:
        """Mengenali kolom, nilai konstan, atau ekspresi dalam kurung"""
        return _DefaultExpression()
