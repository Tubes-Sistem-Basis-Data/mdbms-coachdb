from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from common.catalog import ColumnSchema, DataType, ForeignKey, IndexInfo, IndexKind, TableSchema
from common.expression import Literal
from common.plan import (
    CreateIndex, CreateTable, Delete, DropTable, Filter, Insert, ParsedQuery, PlanNode, QueryKind,
    TableScan, TransactionCommand, Update,
)
from query_optimizer.expression_parser import ExpressionParser
from query_optimizer.lexer import TokenType

if TYPE_CHECKING:
    from query_optimizer.lexer import TokenStream

T = TokenType

# nama tipe dibaca sebagai identifier biasa supaya tidak bentrok dengan nama kolom
_TYPES: dict[str, DataType] = {
    "INTEGER": DataType.INTEGER,
    "INT": DataType.INTEGER,
    "FLOAT": DataType.FLOAT,
    "CHAR": DataType.CHAR,
    "VARCHAR": DataType.VARCHAR,
}


class StatementParser:
    """Parser untuk perintah selain SELECT: UPDATE, INSERT, DELETE, CREATE INDEX, CREATE/DROP TABLE, dan transaksi.

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
        tokens.expect(T.KEYWORD, "CREATE")
        tokens.expect(T.KEYWORD, "INDEX")
        name = tokens.expect(T.IDENT).value
        tokens.expect(T.KEYWORD, "ON")
        table = tokens.expect(T.IDENT).value
        tokens.expect(T.LPAREN, "(")
        column = tokens.expect(T.IDENT).value
        tokens.expect(T.RPAREN, ")")
        kind = IndexKind.BPLUS_TREE
        if tokens.match(T.KEYWORD, "USING"):
            tokens.advance()
            if tokens.match(T.KEYWORD, "BTREE"):
                tokens.advance()
            elif tokens.match(T.KEYWORD, "HASH"):
                tokens.advance()
                kind = IndexKind.HASH
            else:
                tokens.error("diharapkan BTREE atau HASH")
        plan = CreateIndex(index=IndexInfo(name=name, table=table, column=column, kind=kind))
        return ParsedQuery(sql="", kind=QueryKind.CREATE_INDEX, plan=plan)

    def parse_create_table(self, tokens: "TokenStream") -> ParsedQuery:
        """Mengenali CREATE TABLE <tabel> (<definisi kolom atau constraint>, ...)

        Kolom: <nama> <tipe> [PRIMARY KEY] [REFERENCES <tabel>(<kolom>)].
        Constraint: PRIMARY KEY (<kolom>, ...) dan FOREIGN KEY (<kolom>) REFERENCES <tabel>(<kolom>).
        """
        tokens.expect(T.KEYWORD, "CREATE")
        tokens.expect(T.KEYWORD, "TABLE")
        name = tokens.expect(T.IDENT).value
        tokens.expect(T.LPAREN, "(")
        if tokens.match(T.RPAREN):
            tokens.error("tabel minimal punya satu kolom")

        columns: list[ColumnSchema] = []
        primary_key: list[str] = []
        foreign_keys: list[ForeignKey] = []
        while True:
            tok = tokens.peek()
            if tokens.match(T.KEYWORD, "PRIMARY"):
                if primary_key:
                    tokens.error("PRIMARY KEY didefinisikan lebih dari sekali")
                primary_key = self._parse_primary_key(tokens)
            elif tokens.match(T.KEYWORD, "FOREIGN"):
                foreign_keys.append(self._parse_foreign_key(tokens))
            else:
                column, is_pk, fk = self._parse_column_def(tokens)
                columns.append(column)
                if is_pk:
                    if primary_key:
                        tokens.error("PRIMARY KEY didefinisikan lebih dari sekali", tok)
                    primary_key = [column.name]
                if fk is not None:
                    foreign_keys.append(fk)
            if not tokens.match(T.COMMA):
                break
            tokens.advance()
        tokens.expect(T.RPAREN, ")")
        if not columns:
            tokens.error("tabel minimal punya satu kolom")

        # kolom primary key tidak boleh NULL
        columns = [replace(col, nullable=False) if col.name in primary_key else col for col in columns]
        schema = TableSchema(name=name, columns=columns, primary_key=primary_key, foreign_keys=foreign_keys)
        return ParsedQuery(sql="", kind=QueryKind.CREATE_TABLE, plan=CreateTable(schema=schema))

    def parse_drop_table(self, tokens: "TokenStream") -> ParsedQuery:
        """Mengenali DROP TABLE <tabel> [CASCADE | RESTRICT], default RESTRICT"""
        tokens.expect(T.KEYWORD, "DROP")
        tokens.expect(T.KEYWORD, "TABLE")
        table = tokens.expect(T.IDENT).value
        cascade = False
        if tokens.match(T.KEYWORD, "CASCADE"):
            tokens.advance()
            cascade = True
        elif tokens.match(T.KEYWORD, "RESTRICT"):
            tokens.advance()
        return ParsedQuery(sql="", kind=QueryKind.DROP_TABLE, plan=DropTable(table=table, cascade=cascade))

    def parse_transaction(self, tokens: "TokenStream") -> ParsedQuery:
        """Mengenali BEGIN [TRANSACTION], COMMIT, dan ABORT"""
        if tokens.match(T.KEYWORD, "BEGIN"):
            tokens.advance()
            if tokens.match(T.KEYWORD, "TRANSACTION"):
                tokens.advance()
            command = TransactionCommand.BEGIN
        elif tokens.match(T.KEYWORD, "COMMIT"):
            tokens.advance()
            command = TransactionCommand.COMMIT
        elif tokens.match(T.KEYWORD, "ABORT"):
            tokens.advance()
            command = TransactionCommand.ABORT
        else:
            tokens.error("diharapkan BEGIN, COMMIT, atau ABORT")
        return ParsedQuery(sql="", kind=QueryKind.TRANSACTION, command=command)

    def _parse_target(self, tokens: "TokenStream", table: str) -> PlanNode:
        """Sumber baris UPDATE/DELETE: TableScan, dibungkus Filter jika ada WHERE."""
        node: PlanNode = TableScan(table=table)
        if tokens.match(T.KEYWORD, "WHERE"):
            tokens.advance()
            condition = self._expr.parse_condition(tokens)
            node = Filter(condition=condition, children=[node])
        return node

    def _parse_column_def(self, tokens: "TokenStream") -> tuple[ColumnSchema, bool, ForeignKey | None]:
        """<nama> <tipe> diikuti constraint kolom opsional."""
        name = tokens.expect(T.IDENT).value
        data_type, length = self._parse_type(tokens)
        is_pk = False
        fk: ForeignKey | None = None
        while True:
            if tokens.match(T.KEYWORD, "PRIMARY"):
                if is_pk:
                    tokens.error("PRIMARY KEY ditulis dua kali pada kolom yang sama")
                tokens.advance()
                tokens.expect(T.KEYWORD, "KEY")
                is_pk = True
            elif tokens.match(T.KEYWORD, "REFERENCES"):
                if fk is not None:
                    tokens.error("REFERENCES ditulis dua kali pada kolom yang sama")
                ref_table, ref_column = self._parse_reference(tokens)
                fk = ForeignKey(column=name, ref_table=ref_table, ref_column=ref_column)
            else:
                break
        return ColumnSchema(name=name, type=data_type, length=length), is_pk, fk

    def _parse_type(self, tokens: "TokenStream") -> tuple[DataType, int | None]:
        """INTEGER | FLOAT | CHAR(n) | VARCHAR(n)"""
        tok = tokens.peek()
        if tok.type != T.IDENT or str(tok.value).upper() not in _TYPES:
            tokens.error("diharapkan tipe INTEGER, FLOAT, CHAR(n), atau VARCHAR(n)")
        tokens.advance()
        data_type = _TYPES[str(tok.value).upper()]
        if data_type not in (DataType.CHAR, DataType.VARCHAR):
            return data_type, None
        tokens.expect(T.LPAREN, "(")
        size = tokens.expect(T.INTEGER)
        if size.value <= 0:
            tokens.error("panjang kolom harus lebih dari 0", size)
        tokens.expect(T.RPAREN, ")")
        return data_type, size.value

    def _parse_primary_key(self, tokens: "TokenStream") -> list[str]:
        """PRIMARY KEY (<kolom>, ...)"""
        tokens.expect(T.KEYWORD, "PRIMARY")
        tokens.expect(T.KEYWORD, "KEY")
        tokens.expect(T.LPAREN, "(")
        names = [tokens.expect(T.IDENT).value]
        while tokens.match(T.COMMA):
            tokens.advance()
            names.append(tokens.expect(T.IDENT).value)
        tokens.expect(T.RPAREN, ")")
        return names

    def _parse_foreign_key(self, tokens: "TokenStream") -> ForeignKey:
        """FOREIGN KEY (<kolom>) REFERENCES <tabel>(<kolom>)"""
        tokens.expect(T.KEYWORD, "FOREIGN")
        tokens.expect(T.KEYWORD, "KEY")
        tokens.expect(T.LPAREN, "(")
        column = tokens.expect(T.IDENT).value
        if tokens.match(T.COMMA):
            tokens.error("FOREIGN KEY hanya mendukung satu kolom")
        tokens.expect(T.RPAREN, ")")
        ref_table, ref_column = self._parse_reference(tokens)
        return ForeignKey(column=column, ref_table=ref_table, ref_column=ref_column)

    def _parse_reference(self, tokens: "TokenStream") -> tuple[str, str]:
        """REFERENCES <tabel>(<kolom>)"""
        tokens.expect(T.KEYWORD, "REFERENCES")
        table = tokens.expect(T.IDENT).value
        tokens.expect(T.LPAREN, "(")
        column = tokens.expect(T.IDENT).value
        tokens.expect(T.RPAREN, ")")
        return table, column

    def _parse_value(self, tokens: "TokenStream") -> Literal:
        """Satu nilai INSERT. Harus konstanta (angka negatif sudah dilipat jadi Literal)."""
        tok = tokens.peek()
        value = self._expr.parse_expression(tokens)
        if not isinstance(value, Literal):
            tokens.error("nilai INSERT harus berupa konstanta", tok)
        return value