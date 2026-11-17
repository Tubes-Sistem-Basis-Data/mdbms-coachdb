from __future__ import annotations

from copy import deepcopy

from common.catalog import TableSchema
from common.errors import SemanticError, TableNotFoundError


class FakeDDLSM:
    def __init__(self) -> None:
        self._schemas: dict[str, TableSchema] = {}
        self.create_calls: list[TableSchema] = []
        self.create_error: Exception | None = None
        self.drop_calls: list[str] = []
        self.drop_errors: dict[str, Exception] = {}
        self.schema_errors: dict[str, Exception] = {}
        self.list_error: Exception | None = None

    def list_tables(self) -> list[str]:
        if self.list_error is not None:
            raise self.list_error
        
        return sorted(self._schemas)

    def get_schema(self, table: str) -> TableSchema:
        if table in self.schema_errors:
            raise self.schema_errors[table]
        if table not in self._schemas:
            raise TableNotFoundError(f"Tabel '{table}' tidak ditemukan", table)
        
        return deepcopy(self._schemas[table])

    def create_table(self, schema: TableSchema) -> None:
        self.create_calls.append(schema)
        if self.create_error is not None:
            raise self.create_error
        if schema.name in self._schemas:
            raise SemanticError(f"Tabel '{schema.name}' sudah ada", schema.name)
        
        self._schemas[schema.name] = deepcopy(schema)

    def drop_table(self, table: str) -> None:
        self.drop_calls.append(table)
        if table in self.drop_errors:
            raise self.drop_errors[table]
        if table not in self._schemas:
            raise TableNotFoundError(f"Tabel '{table}' tidak ditemukan", table)
        del self._schemas[table]
