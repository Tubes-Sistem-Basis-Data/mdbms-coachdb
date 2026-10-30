from common.expression import Expression
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from query_optimizer.lexer import TokenStream


class ExpressionParser:
    def parse_condition(self, tokens: "TokenStream") -> Expression:
        """Mengenali kondisi WHERE, beberapa kondisi digabung dengan AND"""
        raise NotImplementedError

    def parse_expression(self, tokens: "TokenStream") -> Expression:
        """Mengenali ekspresi aritmetika dengan operator + dan -"""
        raise NotImplementedError

    def _parse_comparison(self, tokens: "TokenStream") -> Expression:
        """Mengenali perbandingan dua ekspresi dengan = <> > >= < <="""
        raise NotImplementedError

    def _parse_term(self, tokens: "TokenStream") -> Expression:
        """Mengenali suku dengan operator * dan /"""
        raise NotImplementedError

    def _parse_unary(self, tokens: "TokenStream") -> Expression:
        """Mengenali tanda minus di depan suatu faktor"""
        raise NotImplementedError

    def _parse_factor(self, tokens: "TokenStream") -> Expression:
        """Mengenali kolom, nilai konstan, atau ekspresi dalam kurung"""
        raise NotImplementedError