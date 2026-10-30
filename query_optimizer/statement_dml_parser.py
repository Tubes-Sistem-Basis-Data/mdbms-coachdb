from common.catalog import DataType
from common.expression import Literal
from common.plan import Delete, Insert, ParsedQuery, QueryKind, TableScan, Update
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from query_optimizer.lexer import TokenStream

class DMLParser:
    def parse_update(self, tokens: "TokenStream") -> ParsedQuery:
        """Mengenali UPDATE <tabel> SET <kolom> = <ekspresi> [WHERE <kondisi>]"""
        node = Update(table="student", column="gpa", expression=Literal(value=4.0, type=DataType.FLOAT),
                      children=[TableScan(table="student")])
        return ParsedQuery(sql="UPDATE student SET gpa = 4.0", kind=QueryKind.UPDATE, plan=node)

    def parse_insert(self, tokens: "TokenStream") -> ParsedQuery:
        """Mengenali INSERT INTO <tabel> VALUES (...) untuk satu baris"""
        values = [Literal(value=1, type=DataType.INTEGER), Literal(value="Andi", type=DataType.VARCHAR)]
        return ParsedQuery(sql="INSERT INTO student VALUES (1, 'Andi')", kind=QueryKind.INSERT,
                           plan=Insert(table="student", values=values))

    def parse_delete(self, tokens: "TokenStream") -> ParsedQuery:
        """Mengenali DELETE FROM <tabel> [WHERE <kondisi>]"""
        node = Delete(table="student", children=[TableScan(table="student")])
        return ParsedQuery(sql="DELETE FROM student", kind=QueryKind.DELETE, plan=node)