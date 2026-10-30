"""QO-04: parser ekspresi dan kondisi (QO2).

Mengubah deretan token menjadi pohon `Expression` dari `common/expression.py`.

Tata bahasa (dari prioritas terendah ke tertinggi):

    condition  := comparison ( AND comparison )*
    comparison := additive [ (= | <> | > | >= | < | <=) additive ]
    additive   := term ( (+ | -) term )*
    term       := unary ( (* | /) unary )*
    unary      := (- | +) unary | factor
    factor     := INTEGER | FLOAT | STRING | kolom | '(' condition ')'
    kolom      := IDENT [ '.' IDENT ]

Kontrak dengan Lexer (QO1): parser hanya memakai `tokens.peek()` dan
`tokens.next()`. Token punya `.type` (enum, dibaca lewat `.type.name`),
`.value`, dan `.col`. Di akhir input `peek()` mengembalikan token EOF.
"""

from __future__ import annotations

from typing import Any

from common.catalog import DataType
from common.errors import QuerySyntaxError
from common.expression import (
    And,
    ArithOp,
    BinaryOp,
    ColumnRef,
    Comparison,
    CompOp,
    Expression,
    Literal,
)

_COMP_OPS: dict[str, CompOp] = {
    "=": CompOp.EQ,
    "<>": CompOp.NE,
    "!=": CompOp.NE,
    ">": CompOp.GT,
    ">=": CompOp.GE,
    "<": CompOp.LT,
    "<=": CompOp.LE,
}
_ADD_OPS: dict[str, ArithOp] = {"+": ArithOp.ADD, "-": ArithOp.SUB}
_MUL_OPS: dict[str, ArithOp] = {"*": ArithOp.MUL, "/": ArithOp.DIV}


def _kind(tok: Any) -> str:
    return tok.type.name


def _is_op(tok: Any, table: dict[str, Any]) -> bool:
    return _kind(tok) == "OP" and tok.value in table


def _is_and(tok: Any) -> bool:
    return _kind(tok) in ("KEYWORD", "IDENT") and str(tok.value).upper() == "AND"


def _is_condition(node: Expression) -> bool:
    return isinstance(node, (Comparison, And))


class ExpressionParser:
    """Parser ekspresi aritmetika dan kondisi WHERE / JOIN ON / SET.

    Dipakai oleh SelectParser (WHERE, JOIN ON) dan StatementParser
    (WHERE, SET). Parser berhenti di token pertama yang bukan bagian
    ekspresi (misalnya ORDER, COMMA, RPAREN, SEMICOLON, EOF) dan tidak
    mengonsumsinya.
    """

    # ------------------------------------------------------------------
    # API publik
    # ------------------------------------------------------------------

    def parse_condition(self, tokens: Any) -> Expression:
        """Parse kondisi: satu `Comparison`, atau `And` jika ada beberapa."""
        node = self._parse_and(tokens)
        if not _is_condition(node):
            raise self._error(
                "Kondisi harus memuat operator perbandingan (=, <>, >, >=, <, <=)",
                tokens.peek(),
            )
        return node

    def parse_expression(self, tokens: Any) -> Expression:
        """Parse ekspresi aritmetika (tanpa operator perbandingan atau AND)."""
        node = self._parse_additive(tokens)
        if _is_condition(node):
            raise self._error("Ekspresi aritmetika tidak boleh berisi kondisi", tokens.peek())
        return node

    # ------------------------------------------------------------------
    # Tingkat kondisi
    # ------------------------------------------------------------------

    def _parse_and(self, tokens: Any) -> Expression:
        operands = [self._parse_comparison(tokens)]
        while _is_and(tokens.peek()):
            tokens.next()
            operands.append(self._parse_comparison(tokens))
        if len(operands) == 1:
            return operands[0]

        flat: list[Expression] = []
        for operand in operands:
            if not _is_condition(operand):
                raise self._error("Operan AND harus berupa kondisi", tokens.peek())
            flat.extend(operand.conditions if isinstance(operand, And) else [operand])
        return And(conditions=flat)

    def _parse_comparison(self, tokens: Any) -> Expression:
        left = self._parse_additive(tokens)
        tok = tokens.peek()
        if not _is_op(tok, _COMP_OPS):
            return left

        tokens.next()
        right = self._parse_additive(tokens)
        if _is_condition(left) or _is_condition(right):
            raise self._error("Operan perbandingan tidak boleh berupa kondisi", tok)
        if _is_op(tokens.peek(), _COMP_OPS):
            raise self._error("Perbandingan beruntun tidak didukung", tokens.peek())
        return Comparison(op=_COMP_OPS[tok.value], left=left, right=right)

    # ------------------------------------------------------------------
    # Tingkat aritmetika
    # ------------------------------------------------------------------

    def _parse_additive(self, tokens: Any) -> Expression:
        left = self._parse_term(tokens)
        while _is_op(tokens.peek(), _ADD_OPS):
            tok = tokens.next()
            right = self._parse_term(tokens)
            left = self._arith(_ADD_OPS[tok.value], left, right, tok)
        return left

    def _parse_term(self, tokens: Any) -> Expression:
        left = self._parse_unary(tokens)
        while _is_op(tokens.peek(), _MUL_OPS):
            tok = tokens.next()
            right = self._parse_unary(tokens)
            left = self._arith(_MUL_OPS[tok.value], left, right, tok)
        return left

    def _parse_unary(self, tokens: Any) -> Expression:
        tok = tokens.peek()
        if _kind(tok) == "OP" and tok.value in ("-", "+"):
            tokens.next()
            operand = self._parse_unary(tokens)
            if _is_condition(operand):
                raise self._error("Tanda +/- tidak dapat dipakai pada kondisi", tok)
            if tok.value == "+":
                return operand
            # Kontrak tidak punya node UnaryMinus: angka dilipat, selain itu 0 - x.
            if isinstance(operand, Literal) and operand.type in (DataType.INTEGER, DataType.FLOAT):
                return Literal(value=-operand.value, type=operand.type)
            return BinaryOp(op=ArithOp.SUB, left=Literal(value=0, type=DataType.INTEGER), right=operand)
        return self._parse_factor(tokens)

    def _parse_factor(self, tokens: Any) -> Expression:
        tok = tokens.peek()
        kind = _kind(tok)

        if kind == "INTEGER":
            tokens.next()
            return Literal(value=int(tok.value), type=DataType.INTEGER)
        if kind == "FLOAT":
            tokens.next()
            return Literal(value=float(tok.value), type=DataType.FLOAT)
        if kind == "STRING":
            tokens.next()
            return Literal(value=str(tok.value), type=DataType.VARCHAR)
        if kind == "IDENT":
            return self._parse_column(tokens)
        if kind == "LPAREN":
            tokens.next()
            inner = self._parse_and(tokens)
            self._expect(tokens, "RPAREN", "')'")
            return inner
        raise self._error(f"Diharapkan nilai, kolom, atau '(' tetapi menemukan {self._describe(tok)}", tok)

    def _parse_column(self, tokens: Any) -> ColumnRef:
        first = tokens.next()
        if _kind(tokens.peek()) == "DOT":
            tokens.next()
            second = self._expect(tokens, "IDENT", "nama kolom setelah '.'")
            return ColumnRef(table=str(first.value).lower(), column=str(second.value).lower())
        return ColumnRef(table=None, column=str(first.value).lower())

    # ------------------------------------------------------------------
    # Pembantu
    # ------------------------------------------------------------------

    def _arith(self, op: ArithOp, left: Expression, right: Expression, tok: Any) -> Expression:
        if _is_condition(left) or _is_condition(right):
            raise self._error("Operan aritmetika tidak boleh berupa kondisi", tok)
        return BinaryOp(op=op, left=left, right=right)

    def _expect(self, tokens: Any, kind: str, what: str) -> Any:
        tok = tokens.peek()
        if _kind(tok) != kind:
            raise self._error(f"Diharapkan {what} tetapi menemukan {self._describe(tok)}", tok)
        return tokens.next()

    @staticmethod
    def _describe(tok: Any) -> str:
        return "akhir query" if _kind(tok) == "EOF" else f"'{tok.value}'"

    @staticmethod
    def _error(message: str, tok: Any) -> QuerySyntaxError:
        value = "" if _kind(tok) == "EOF" or tok.value is None else str(tok.value)
        return QuerySyntaxError(message, position=getattr(tok, "col", 0), token=value)