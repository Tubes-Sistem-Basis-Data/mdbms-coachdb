from __future__ import annotations

from typing import TYPE_CHECKING

from common.catalog import ColumnSchema, TableSchema
from common.errors import SemanticError, TableNotFoundError
from common.result import ExecutionResult
from query_processor.errors import PlanError
from query_processor.operators.base import Executable

if TYPE_CHECKING:
    from query_processor.context import ExecContext


class CreateTableExecution(Executable):
    """Meneruskan skema hasil binding QO ke SM melalui jalur administrasi."""

    def __init__(self, schema: TableSchema) -> None:
        if not isinstance(schema, TableSchema):
            raise PlanError("CREATE TABLE memerlukan TableSchema")
        if not isinstance(schema.name, str) or not schema.name.strip():
            raise PlanError("Nama tabel CREATE TABLE wajib terisi")
        if (
            not isinstance(schema.columns, list)
            or not schema.columns
            or not all(isinstance(column, ColumnSchema) for column in schema.columns)
        ):
            raise PlanError("CREATE TABLE memerlukan daftar ColumnSchema yang tidak kosong")
        
        self._schema = schema

    def execute(self, ctx: ExecContext) -> ExecutionResult:
        ctx.check_cancelled()
        ctx.sm.create_table(self._schema)
        return ExecutionResult(
            ok=True,
            affected_rows=0,
            message=f"Tabel '{self._schema.name}' dibuat",
        )


class DropTableExecution(Executable):
    """Hapus target dan tabel yang bergantung untuk CASCADE."""

    def __init__(self, table: str, cascade: bool = False) -> None:
        if not isinstance(table, str) or not table.strip():
            raise PlanError("Nama tabel DROP TABLE wajib terisi")
        if not isinstance(cascade, bool):
            raise PlanError("Opsi cascade DROP TABLE harus berupa bool")
        
        self._table = table
        self._cascade = cascade

    def execute(self, ctx: ExecContext) -> ExecutionResult:
        ctx.check_cancelled()
        tables = sorted(set(ctx.sm.list_tables()))
        if self._table not in tables:
            raise TableNotFoundError(f"Tabel '{self._table}' tidak ditemukan", self._table)

        # Arah edge: tabel dirujuk -> tabel merujuk.
        dependents: dict[str, set[str]] = {table: set() for table in tables}
        for table in tables:
            ctx.check_cancelled()
            schema = ctx.sm.get_schema(table)
            for foreign_key in schema.foreign_keys:
                if foreign_key.ref_table not in dependents:
                    raise SemanticError(
                        f"Foreign key tabel '{table}' merujuk tabel "
                        f"'{foreign_key.ref_table}' yang tidak ditemukan",
                        table,
                    )
                    
                dependents[foreign_key.ref_table].add(table)

        if self._cascade:
            order = self._drop_order(dependents)
        else:
            referring = sorted(dependents[self._table] - {self._table})
            if referring:
                raise SemanticError(
                    f"DROP TABLE '{self._table}' RESTRICT ditolak: "
                    f"masih dirujuk oleh {', '.join(referring)}",
                    self._table,
                )
                
            order = [self._table]

        for table in order:
            ctx.check_cancelled()
            ctx.sm.drop_table(table)
            
        return ExecutionResult(
            ok=True,
            affected_rows=0,
            message=f"Tabel dihapus: {', '.join(order)}",
        )

    def _drop_order(self, dependents: dict[str, set[str]]) -> list[str]:
        # DFS postorder iteratif: perujuk lebih lebih dulu pada graph tanpa siklus.
        visited: set[str] = set()
        order: list[str] = []
        stack = [(self._table, False)]
        
        while stack:
            table, expanded = stack.pop()
            if expanded:
                order.append(table)
                
            elif table not in visited:
                visited.add(table)
                stack.append((table, True))
                stack.extend((child, False) for child in sorted(dependents[table], reverse=True))
                
        return order
