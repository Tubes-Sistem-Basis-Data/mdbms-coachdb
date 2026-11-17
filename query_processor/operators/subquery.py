"""Operator sumber untuk subquery di klausa FROM (QP4)."""

from __future__ import annotations

from query_processor.context import ExecContext
from query_processor.errors import ExecutionError, PlanError
from query_processor.operators.base import RowOperator
from query_processor.tuple import ExecRow, OutputSchema


class SubqueryScanOp(RowOperator):
    """Ekspos baris child sebagai relasi turunan dengan satu alias.

    Skema hasil disiapkan translator: qualifier = alias subquery, nama = nama
    ekspor child, tipe tetap. Operator hanya mengganti skema setiap baris secara
    streaming dan tidak meneruskan RID karena relasi turunan bukan target DML.
    """

    def __init__(self, child: RowOperator, alias: str, schema: OutputSchema) -> None:
        if not isinstance(alias, str) or not alias.strip():
            raise PlanError("Subquery di FROM wajib memakai alias")
        if not isinstance(schema, OutputSchema) or not isinstance(schema.columns, list):
            raise PlanError("Skema subquery di FROM harus berupa OutputSchema")
        inner = child.schema.columns
        if len(schema.columns) != len(inner):
            raise PlanError("Lebar skema subquery di FROM tidak sesuai dengan child")
        if schema.labels is not None:
            raise PlanError("Skema subquery di FROM tidak memakai label tampilan")
        names: set[str] = set()
        for binding, source in zip(schema.columns, inner):
            if binding.qualifier != alias or binding.alternate_qualifiers:
                raise PlanError("Kolom subquery di FROM hanya boleh memakai alias subquery")
            if binding.type != source.type:
                raise PlanError("Tipe kolom subquery di FROM tidak sesuai dengan child")
            if binding.name in names:
                raise PlanError(f"Nama kolom '{binding.name}' berulang pada subquery '{alias}'")
            names.add(binding.name)
        self._child = child
        self._alias = alias
        self._schema = schema
        self._child_open = False

    @property
    def schema(self) -> OutputSchema:
        return self._schema

    def open(self, ctx: ExecContext) -> None:
        """Mulai scan baru; open ulang mereset state child."""
        if self._child_open:
            self.close()
        self._child_open = True
        try:
            self._child.open(ctx)
        except BaseException:
            self._close_quietly()
            raise

    def next(self) -> ExecRow | None:
        """Satu baris child dengan skema subquery dan rid=None."""
        if not self._child_open:
            return None
        try:
            row = self._child.next()
            if row is None:
                return None
            self._validate_row(row)
            return ExecRow(row.values, self._schema, rid=None)
        except BaseException:
            self._close_quietly()
            raise

    def close(self) -> None:
        """Idempoten: child ditutup paling banyak sekali per siklus open."""
        if self._child_open:
            self._child_open = False
            self._child.close()

    def _close_quietly(self) -> None:
        # Error cleanup tidak boleh menggantikan error utama.
        try:
            self.close()
        except BaseException:
            pass

    def _validate_row(self, row: ExecRow) -> None:
        if (
            not isinstance(row, ExecRow)
            or not isinstance(row.values, tuple)
            or len(row.values) != len(self._schema.columns)
        ):
            raise ExecutionError(f"Baris subquery '{self._alias}' tidak sesuai skema")
