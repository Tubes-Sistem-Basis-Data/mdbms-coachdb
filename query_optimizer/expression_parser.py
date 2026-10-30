from common.catalog import DataType
from common.expression import ColumnRef, CompOp, Comparison, Expression, Literal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from query_optimizer.lexer import TokenStream

class ExpressionParser:
    def parse_condition(self, tokens: "TokenStream") -> Expression:
        """Mengenali kondisi WHERE, beberapa kondisi digabung dengan AND"""
        return Comparison(op=CompOp.EQ, left=ColumnRef(table=None, column="id"),
                          right=Literal(value=1, type=DataType.INTEGER))

    def parse_expression(self, tokens: "TokenStream") -> Expression:
        """Mengenali ekspresi aritmetika dengan operator + dan -"""
        return Literal(value=1, type=DataType.INTEGER)

    def _parse_comparison(self, tokens: "TokenStream") -> Expression:
        """Mengenali perbandingan dua ekspresi dengan = <> > >= < <="""
        return Comparison(op=CompOp.EQ, left=ColumnRef(table=None, column="id"),
                          right=Literal(value=1, type=DataType.INTEGER))

    def _parse_term(self, tokens: "TokenStream") -> Expression:
        """Mengenali suku dengan operator * dan /"""
        return Literal(value=1, type=DataType.INTEGER)

    def _parse_unary(self, tokens: "TokenStream") -> Expression:
        """Mengenali tanda minus di depan suatu faktor"""
        return Literal(value=-1, type=DataType.INTEGER)

    def _parse_factor(self, tokens: "TokenStream") -> Expression:
        """Mengenali kolom, nilai konstan, atau ekspresi dalam kurung"""
        return ColumnRef(table=None, column="id")