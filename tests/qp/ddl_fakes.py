from __future__ import annotations

from copy import deepcopy

from common.catalog import TableSchema
from common.errors import SemanticError, TableNotFoundError


class FakeDDLSM:
    def __init__(self) -> None:
        self._schemas: dict[str, TableSchema] = {}
        self.create_calls: list[TableSchema] = []
        self.create_error: Exception | None = None

    def list_tables(self) -> list[str]:
        return sorted(self._schemas)

    def get_schema(self, table: str) -> TableSchema:
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
