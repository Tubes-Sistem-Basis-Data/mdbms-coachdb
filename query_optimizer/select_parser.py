from typing import TYPE_CHECKING

from common.expression import ColumnRef, Expression
from common.plan import Filter, Join, JoinKind, ParsedQuery, PlanNode, Project, QueryKind, Sort, TableScan
from query_optimizer.expression_parser import ExpressionParser
from query_optimizer.lexer import TokenType

if TYPE_CHECKING:
    from query_optimizer.lexer import TokenStream

T = TokenType

class SelectParser:

    def __init__(self, expr_parser: ExpressionParser) -> None:
        self._expr = expr_parser
    # TODO(QO-02): tambahkan __init__(self, expr_parser: ExpressionParser) dan simpan di
    # self._expr. Keputusan: ParserDispatcher yang membuat satu ExpressionParser lalu
    # menyuntikkannya, SelectParser tidak membuat sendiri. Dengan begitu unit test bisa
    # memakai expression parser palsu sebelum milik QO2 selesai.

    # Grammar:
    # select_stmt   := SELECT select_list FROM from_list [WHERE condition]
    #                  [ORDER BY column_ref [ASC | DESC]]
    # select_list   := "*" | column_ref { "," column_ref }
    # column_ref    := IDENT [ "." IDENT ]
    # from_list     := table_ref { "," table_ref }
    # table_ref     := table_primary { JOIN table_primary ON condition
    #                                | NATURAL JOIN table_primary }
    # table_primary := IDENT
    #
    # Keputusan bersama QO2 untuk parse_condition:
    # - berhenti tanpa mengonsumsi saat ketemu token di luar ekspresi (ORDER, JOIN,
    #   NATURAL, ',', ';', ')' di luar kurung, EOF), jadi parser ini lanjut dari situ
    # - kalau kondisinya hanya satu, yang dikembalikan kondisinya langsung, bukan And([x])
    # Semua error sintaks dilempar lewat tokens.error(msg, tok).

    def parse_select(self, tokens: "TokenStream") -> ParsedQuery:
        """Mengenali SELECT ... FROM ... [WHERE] [ORDER BY] [LIMIT]"""
        # TODO(QO-02): bangun tree dari bawah ke atas, Project -> Sort -> Filter -> FROM.
        columns = []
        # SELECT
        tokens.expect(T.KEYWORD, "SELECT")
        if tokens.peek().value == "*" and tokens.peek().type == T.OP:
            tokens.advance()
        # SKIP KOLOM SELECT
        elif tokens.peek().value == "FROM" and tokens.peek().type == T.KEYWORD:
            tokens.error("daftar kolom kosong!")

        # SCAN KOLOM SELECT 
        else:
            columns.append(self._parse_column_ref(tokens))
            while tokens.match(T.COMMA):
                tokens.advance()
                columns.append(self._parse_column_ref(tokens))
            # DIIKUTI FROM {TABLE}
            tokens.expect(T.KEYWORD, "FROM")
            node = self._parse_from(tokens)
        # WHERE
        if (tokens.peek().type == T.KEYWORD and tokens.peek().value == "WHERE"):
            tokens.advance()
            cond = self._expr.parse_condition(tokens)
            node = Filter(condition=cond, children=[node])
        # ORDER
        if (tokens.peek().type == T.KEYWORD and tokens.peek().value == "ORDER"):
            tokens.advance()
            tokens.expect(T.KEYWORD, "BY")
            node = self._parse_order_by(tokens, node)
            plan = Project(columns=columns, children=[node])


        # 1. tokens.expect(KEYWORD, "SELECT")
        # 2. Select list:
        #    - OP '*' -> advance, columns = [] (keputusan: list kosong = SELECT *,
        #      Binder QO3 yang mengekspansi)
        #    - token berikutnya langsung FROM -> tokens.error("daftar kolom kosong")
        #    - selain itu: columns = [_parse_column_ref], ulangi selama ada COMMA
        # 3. tokens.expect(KEYWORD, "FROM"); node = self._parse_from(tokens)
        # 4. Jika ada KEYWORD WHERE: advance, cond = self._expr.parse_condition(tokens),

        #    node = Filter(condition=cond, children=[node])
        # 5. Jika ada KEYWORD ORDER: advance, expect BY, node = self._parse_order_by(tokens, node)
        # 6. plan = Project(columns=columns, children=[node])
        # 7. return ParsedQuery(sql="", kind=QueryKind.SELECT, plan=plan)
        #    Keputusan: sql dibiarkan "" (diisi dispatcher), ';' dan EOF juga dicek
        #    dispatcher, jadi jangan dicek di sini.
        return ParsedQuery(sql="", kind=QueryKind.SELECT)

    def _parse_column_ref(self, tokens: "TokenStream") -> ColumnRef:
        """Mengenali column_ref := IDENT [ "." IDENT ]"""
        # Keputusan: kolom tanpa prefix tetap table=None, Binder QO3 yang mengisi.
        pertama = tokens.expect(T.IDENT).value
        if tokens.match(T.DOT):
            tokens.advance()
            kolom = tokens.expect(T.IDENT).value
            return ColumnRef(table=pertama, column=kolom)
        return ColumnRef(table=None, column=pertama)

    def _parse_from(self, tokens: "TokenStream") -> PlanNode:
        """Mengenali klausa FROM, termasuk JOIN, NATURAL JOIN, dan cartesian"""
        kiri = self._parse_table_ref(tokens)
        while(tokens.peek().type == T.COMMA):
            tokens.advance()
            kanan = self._parse_table_ref(tokens)
            kiri = Join(kind=JoinKind.CROSS, children=[kiri, kanan])
        return kiri

    def _parse_table_ref(self, tokens: "TokenStream") -> PlanNode:
        kiri = TableScan(table=tokens.expect(T.IDENT).value)
        while True:
            if (tokens.peek().type == T.KEYWORD and tokens.peek().value == "NATURAL"):
                tokens.advance()
                tokens.expect(T.KEYWORD, "JOIN")
                kanan = TableScan(table=tokens.expect(T.IDENT).value)
                kiri = Join(kind = JoinKind.NATURAL, children=[kiri, kanan])
            elif (tokens.peek().type == T.KEYWORD and tokens.peek().value == "JOIN"):
                tokens.advance()
                kanan = TableScan(table=tokens.expect(T.IDENT).value)
                tokens.expect(T.KEYWORD, "ON")
                cond = self._expr.parse_condition(tokens)
                kiri = Join(kind=JoinKind.THETA, condition=cond, children=[kiri, kanan])
            else:
                break
        return kiri
    # TODO(QO-03): tambahkan helper _parse_table_ref(tokens) -> PlanNode
    # left = TableScan(table=tokens.expect(IDENT).value)
    # loop:
    #   - KEYWORD NATURAL: advance, expect JOIN, right = TableScan(...),
    #     left = Join(kind=JoinKind.NATURAL, children=[left, right])
    #   - KEYWORD JOIN: advance, right = TableScan(...), expect ON,
    #     cond = self._expr.parse_condition(tokens),
    #     left = Join(kind=JoinKind.THETA, condition=cond, children=[left, right])
    #   - selain itu: break
    # return left
    # Error yang harus muncul: "a JOIN b;" -> diharapkan ON, "a NATURAL b" -> diharapkan JOIN.

    def _parse_order_by(self, tokens: "TokenStream", child: PlanNode) -> Sort:
        col = self._parse_column_ref(tokens)
        asc = True
        if (tokens.peek().type == T.KEYWORD and tokens.peek().value == "DESC"):
            asc = False
            tokens.advance()
        elif (tokens.peek().type == T.KEYWORD and tokens.peek().value == "ASC"):
            tokens.advance()
        return Sort(column=col, ascending=asc, children=[child])
        """Mengenali klausa ORDER BY"""
        # TODO(QO-02): ubah signature jadi (tokens, child: PlanNode) -> Sort.
        # Dipanggil setelah ORDER BY dikonsumsi.
        # col = self._parse_column_ref(tokens)
        # ascending = True; jika KEYWORD DESC: advance, ascending = False;
        # jika KEYWORD ASC: advance.
        # return Sort(column=col, ascending=ascending, children=[child])

    def _parse_limit_alias(self, tokens: "TokenStream") -> PlanNode:
        """Mengenali klausa LIMIT dan alias"""
        # TODO(QO-17, bonus): common.plan belum punya node Limit dan TableScan belum punya
        # field alias. Kerjakan hanya setelah kontrak diubah lewat PR scope global.
        return PlanNode()

