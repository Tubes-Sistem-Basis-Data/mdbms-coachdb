from typing import TYPE_CHECKING

from common.expression import ColumnRef
from common.plan import Filter, Join, JoinKind, ParsedQuery, PlanNode, Project, QueryKind, Sort, TableScan
from query_optimizer.expression_parser import ExpressionParser
from query_optimizer.lexer import TokenType

if TYPE_CHECKING:
    from query_optimizer.lexer import TokenStream

T = TokenType


class SelectParser:
    """Parser SELECT recursive descent. Dipanggil ParserDispatcher, memakai ExpressionParser untuk WHERE dan ON."""

    def __init__(self, expr_parser: ExpressionParser) -> None:
        self._expr = expr_parser

    def parse_select(self, tokens: "TokenStream") -> ParsedQuery:
        tokens.expect(T.KEYWORD, "SELECT")
        columns: list[ColumnRef] = []
        if tokens.match(T.OP, "*"):
            tokens.advance()
        elif tokens.match(T.KEYWORD, "FROM"):
            tokens.error("daftar kolom kosong")
        else:
            columns.append(self._parse_column_ref(tokens))
            while tokens.match(T.COMMA):
                tokens.advance()
                columns.append(self._parse_column_ref(tokens))

        tokens.expect(T.KEYWORD, "FROM")
        node = self._parse_from(tokens)

        if tokens.match(T.KEYWORD, "WHERE"):
            tokens.advance()
            cond = self._expr.parse_condition(tokens)
            node = Filter(condition=cond, children=[node])

        if tokens.match(T.KEYWORD, "ORDER"):
            tokens.advance()
            tokens.expect(T.KEYWORD, "BY")
            node = self._parse_order_by(tokens, node)

        plan = Project(columns=columns, children=[node])
        return ParsedQuery(sql="", kind=QueryKind.SELECT, plan=plan)

    def _parse_column_ref(self, tokens: "TokenStream") -> ColumnRef:
        pertama = tokens.expect(T.IDENT).value
        if tokens.match(T.DOT):
            tokens.advance()
            kolom = tokens.expect(T.IDENT).value
            return ColumnRef(table=pertama, column=kolom)
        return ColumnRef(table=None, column=pertama)

    def _parse_from(self, tokens: "TokenStream") -> PlanNode:
        left = self._parse_table_ref(tokens)
        while tokens.match(T.COMMA):
            tokens.advance()
            right = self._parse_table_ref(tokens)
            left = Join(kind=JoinKind.CROSS, children=[left, right])
        return left

    def _parse_table_ref(self, tokens: "TokenStream") -> PlanNode:
        left: PlanNode = self._parse_table(tokens)
        while True:
            if tokens.match(T.KEYWORD, "NATURAL"):
                tokens.advance()
                tokens.expect(T.KEYWORD, "JOIN")
                right = self._parse_table(tokens)
                left = Join(kind=JoinKind.NATURAL, children=[left, right])
            elif tokens.match(T.KEYWORD, "JOIN"):
                tokens.advance()
                right = self._parse_table(tokens)
                tokens.expect(T.KEYWORD, "ON")
                cond = self._expr.parse_condition(tokens)
                left = Join(kind=JoinKind.THETA, condition=cond, children=[left, right])
            else:
                return left

    def _parse_table(self, tokens: "TokenStream") -> TableScan:
        return TableScan(table=tokens.expect(T.IDENT).value)

    def _parse_order_by(self, tokens: "TokenStream", child: PlanNode) -> Sort:
        col = self._parse_column_ref(tokens)
        ascending = True
        if tokens.match(T.KEYWORD, "DESC"):
            tokens.advance()
            ascending = False
        elif tokens.match(T.KEYWORD, "ASC"):
            tokens.advance()
        return Sort(column=col, ascending=ascending, children=[child])

    def _parse_limit_alias(self, tokens: "TokenStream") -> PlanNode:
        return PlanNode()
