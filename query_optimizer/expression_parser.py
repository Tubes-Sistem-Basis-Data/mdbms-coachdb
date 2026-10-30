"""QO-04: Mengenali Ekspresi dan Kondisi.

PIC: QO2 (Arina)
Status: STUB — menunggu implementasi.
"""

from __future__ import annotations

from common.expression import Expression
from query_optimizer.lexer import TokenStream


class ExpressionParser:
    """Parser untuk ekspresi aritmetika dan kondisi WHERE / ON.

    TODO (QO2): Implementasikan parsing ekspresi lengkap.
    - parse_condition: kondisi WHERE (perbandingan, AND, kurung)
    - parse_expression: ekspresi aritmetika (+ - * /, unary minus)
    """

    def parse_condition(self, tokens: TokenStream) -> Expression:
        """Parse kondisi (WHERE / ON). Kembalikan pohon Expression.

        Raise QuerySyntaxError jika sintaks tidak valid.
        """
        raise NotImplementedError("ExpressionParser.parse_condition belum diimplementasi (QO-04)")

    def parse_expression(self, tokens: TokenStream) -> Expression:
        """Parse ekspresi aritmetika. Kembalikan pohon Expression.

        Raise QuerySyntaxError jika sintaks tidak valid.
        """
        raise NotImplementedError("ExpressionParser.parse_expression belum diimplementasi (QO-04)")

