from __future__ import annotations

from typing import TYPE_CHECKING

from common.catalog import ColumnSchema, TableSchema
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
