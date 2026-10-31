from query_optimizer.statement_dml_parser import DMLParser
from query_optimizer.statement_txn_index_parser import TxnIndexParser


class StatementParser(DMLParser, TxnIndexParser):
    """Gabungan 5 method di diagram: parse_update, parse_insert, parse_delete,
    parse_create_index, parse_transaction"""
    pass
