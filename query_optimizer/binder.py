from __future__ import annotations

from dataclasses import dataclass, replace
from math import isfinite

from common.catalog import ColumnSchema, DataType, IndexInfo, IndexKind, TableSchema
from common.errors import SemanticError
from common.expression import (
    And, ArithOp, BinaryOp, ColumnRef, Comparison, CompOp, Expression, Literal, Subquery,
)
from common.interfaces import StorageManager
from common.plan import (
    CreateIndex, CreateTable, Delete, DropTable, Filter, IndexScan, Insert, Join, JoinKind, Limit,
    ParsedQuery, PlanNode, Project, QueryKind, Sort, SubqueryScan, TableScan, Update,
)
from query_optimizer.plan_utils import output_names, source_list


_NUMERIC = (DataType.INTEGER, DataType.FLOAT)
_TEXT = (DataType.CHAR, DataType.VARCHAR)
_ARITY = {
    TableScan: 0, IndexScan: 0, Filter: 1, Project: 1, Join: 2, Sort: 1, Limit: 1, SubqueryScan: 1,
    Insert: 0, Update: 1, Delete: 1, CreateIndex: 0, CreateTable: 0, DropTable: 0,
}
_ROW_NODES = (TableScan, IndexScan, Filter, Project, Join, Sort, Limit, SubqueryScan)


@dataclass(frozen=True)
class _BoundColumn:
    table: str
    schema: ColumnSchema
    qualifiers: tuple[str, ...]

    def ref(self) -> ColumnRef:
        return ColumnRef(self.table, self.schema.name)


def _compatible(left: DataType, right: DataType) -> bool:
    return (left in _NUMERIC and right in _NUMERIC) or (left in _TEXT and right in _TEXT)


def _source_tables(node: PlanNode) -> set[str]:
    """Asal tabel (atau aliasnya) tetap ada walaupun kolomnya dibuang oleh Project."""
    return set(source_list(node))


class Binder:
    """Mengisi dan memvalidasi nama tabel dan kolom di query (QO-07)."""

    def __init__(self, catalog: StorageManager | None = None) -> None:
        self._catalog = catalog

    def bind(self, query: ParsedQuery) -> ParsedQuery:
        """Validasi dan isi plan di tempat katalog dibaca ulang untuk setiap query

        Hanya memakai get_schema dari facade SM. Error katalog, termasuk turunan
        SemanticError seperti TableNotFoundError, diteruskan ke pemanggil.
        """
        if query.plan is None:
            if query.kind is QueryKind.TRANSACTION:
                return query
            raise SemanticError("Query data membutuhkan plan")
        if self._catalog is None:
            raise SemanticError("Binder membutuhkan katalog StorageManager")
        try:
            self._bind_plan(query.plan, {}, set())
        except RecursionError as exc:
            raise SemanticError("Pohon plan atau ekspresi terlalu dalam") from exc
        return query

    def _schema(self, table: str, schemas: dict[str, TableSchema]) -> TableSchema:
        if table not in schemas:
            assert self._catalog is not None
            schemas[table] = self._catalog.get_schema(table)
        return schemas[table]

    def _bind_plan(
        self, node: PlanNode, schemas: dict[str, TableSchema], active: set[int],
    ) -> list[_BoundColumn]:
        arity = _ARITY.get(type(node))
        if arity is None:
            raise SemanticError(f"Node plan '{type(node).__name__}' tidak didukung binder")
        if id(node) in active:
            raise SemanticError("Pohon plan mengandung siklus")
        if len(node.children) != arity:
            raise SemanticError(f"{type(node).__name__} membutuhkan {arity} anak")
        if any(type(child) not in _ROW_NODES for child in node.children):
            raise SemanticError("Anak operator harus menghasilkan baris")
        active.add(id(node))
        try:
            children = [self._bind_plan(child, schemas, active) for child in node.children]
            if isinstance(node, (TableScan, IndexScan)):
                schema = self._schema(node.table, schemas)
                name = node.table
                if node.alias is not None:
                    if not isinstance(node.alias, str) or not node.alias.strip():
                        raise SemanticError("Alias tabel tidak valid", node.table)
                    name = node.alias
                output = [_BoundColumn(name, col, (name,)) for col in schema.columns]
                if isinstance(node, IndexScan):
                    # metadata index merujuk nama tabel asli, bukan alias
                    original = [_BoundColumn(node.table, col, (node.table,)) for col in schema.columns]
                    self._bind_index_scan(node, schema, original)
            elif isinstance(node, SubqueryScan):
                output = self._subquery_scan_columns(node, children[0])
            elif isinstance(node, CreateTable):
                self._bind_create_table(node.schema, schemas)
                output = []
            elif isinstance(node, DropTable):
                self._schema(node.table, schemas)
                if type(node.cascade) is not bool:
                    raise SemanticError("Opsi CASCADE pada DROP TABLE tidak valid", node.table)
                output = []
            elif isinstance(node, (Insert, Update, Delete, CreateIndex)):
                self._bind_write(node, children, schemas)
                output = []
            elif isinstance(node, Join):
                left, right = children
                if _source_tables(node.children[0]) & _source_tables(node.children[1]):
                    raise SemanticError("Tabel berulang membutuhkan alias berbeda")
                output = left + right
                if node.kind is JoinKind.THETA:
                    self._bind_condition(node.condition, output)
                elif node.kind in (JoinKind.CROSS, JoinKind.NATURAL):
                    if node.condition is not None:
                        raise SemanticError("Join CROSS/NATURAL tidak menerima kondisi ON")
                    if node.kind is JoinKind.NATURAL:
                        output = self._natural_columns(left, right)
                else:
                    raise SemanticError("Jenis join tidak dikenal")
            else:
                output = children[0]
                if isinstance(node, Filter):
                    self._bind_condition(node.condition, output)
                elif isinstance(node, Sort):
                    self._resolve(node.column, output)
                elif isinstance(node, Limit):
                    if type(node.count) is not int or node.count < 0:
                        raise SemanticError("Nilai LIMIT harus bilangan bulat tidak negatif")
                elif isinstance(node, Project):
                    if not node.columns:
                        if node.aliases:
                            raise SemanticError("Alias kolom tidak bisa dipakai dengan SELECT *")
                        node.columns = [col.ref() for col in output]
                    output = [self._resolve(ref, output) for ref in node.columns]
                    self._check_aliases(node)
            node.output_columns = [col.ref() for col in output]
            return output
        finally:
            active.remove(id(node))

    def _bind_write(
        self, node: PlanNode, children: list[list[_BoundColumn]], schemas: dict[str, TableSchema],
    ) -> None:
        table = node.index.table if isinstance(node, CreateIndex) else node.table
        schema = self._schema(table, schemas)
        scope = [_BoundColumn(table, col, (table,)) for col in schema.columns]
        if isinstance(node, CreateIndex):
            self._validate_index(node.index, scope)
            if any(index.name == node.index.name or index.column == node.index.column for index in schema.indexes):
                raise SemanticError("Nama index atau kolom yang diindeks sudah terdaftar", node.index.name)
        elif isinstance(node, Insert):
            if len(node.values) != len(schema.columns):
                raise SemanticError("Jumlah nilai INSERT tidak sesuai skema tabel", table)
            for value, column in zip(node.values, schema.columns):
                if not isinstance(value, Literal):
                    raise SemanticError("INSERT hanya menerima nilai literal")
                self._check_assignment(column, self._bind_value(value, []), value)
        else:
            source = node.children[0]
            while isinstance(source, Filter):
                source = source.children[0]
            if not isinstance(source, (TableScan, IndexScan)) or source.table != table:
                raise SemanticError("Sumber UPDATE/DELETE harus scan tabel target dengan Filter opsional", table)
            if isinstance(node, Update):
                column = self._resolve(ColumnRef(table, node.column), scope).schema
                value_type = self._bind_value(node.expression, children[0])
                self._check_assignment(column, value_type, node.expression)

    def _check_assignment(self, column: ColumnSchema, value_type: DataType, expr: Expression) -> None:
        if isinstance(expr, Literal) and expr.value is None:
            if not column.nullable:
                raise SemanticError(f"Kolom '{column.name}' tidak menerima NULL", column.name)
            return
        if not _compatible(column.type, value_type) or (column.type is DataType.INTEGER and value_type is DataType.FLOAT):
            raise SemanticError(f"Tipe nilai tidak sesuai kolom '{column.name}'", column.name)
        if (
            isinstance(expr, Literal) and isinstance(expr.value, str)
            and column.length is not None and len(expr.value) > column.length
        ):
            raise SemanticError(f"Nilai melampaui panjang kolom '{column.name}'", column.name)

    def _validate_index(self, index: IndexInfo, scope: list[_BoundColumn]) -> _BoundColumn:
        if not index.name.strip() or not isinstance(index.kind, IndexKind):
            raise SemanticError("Nama atau jenis index tidak valid", index.name)
        return self._resolve(ColumnRef(index.table, index.column), scope)

    def _bind_index_scan(self, node: IndexScan, schema: TableSchema, scope: list[_BoundColumn]) -> None:
        column = self._validate_index(node.index, scope)
        if node.index.table != node.table or node.index not in schema.indexes:
            raise SemanticError("Metadata IndexScan tidak sesuai katalog", node.index.name)
        if node.op not in (CompOp.EQ, CompOp.GT, CompOp.GE, CompOp.LT, CompOp.LE):
            raise SemanticError("Operator IndexScan tidak didukung")
        if node.index.kind is IndexKind.HASH and node.op is not CompOp.EQ:
            raise SemanticError("Index HASH hanya mendukung kesamaan")
        if node.op is CompOp.EQ:
            if node.low is not None or node.high is not None:
                raise SemanticError("IndexScan kesamaan memakai key, bukan low/high")
            values = [node.key]
        else:
            bound = node.low if node.op in (CompOp.GT, CompOp.GE) else node.high
            if node.key is not None or bound is None:
                raise SemanticError("IndexScan rentang membutuhkan batas low/high yang sesuai")
            values = [node.low, node.high]
        for value in values:
            if value is None:
                continue
            value_type = {int: DataType.INTEGER, float: DataType.FLOAT, str: DataType.VARCHAR}.get(type(value))
            if value_type is None:
                raise SemanticError("Tipe kunci index tidak valid")
            self._bind_value(Literal(value, value_type), [])
            if not _compatible(column.schema.type, value_type):
                raise SemanticError("Tipe kunci index tidak sesuai kolom", node.index.column)

    def _natural_columns(
        self, left: list[_BoundColumn], right: list[_BoundColumn],
    ) -> list[_BoundColumn]:
        output = list(left)
        for column in right:
            matches = [i for i, col in enumerate(left) if col.schema.name == column.schema.name]
            if not matches:
                output.append(column)
                continue
            if len(matches) != 1 or sum(c.schema.name == column.schema.name for c in right) != 1:
                raise SemanticError(f"Kolom NATURAL JOIN '{column.schema.name}' ambigu", column.schema.name)
            pos = matches[0]
            original = output[pos]
            if not _compatible(original.schema.type, column.schema.type):
                raise SemanticError(f"Tipe kolom NATURAL JOIN '{column.schema.name}' tidak kompatibel")
            output[pos] = replace(original, qualifiers=original.qualifiers + column.qualifiers)
        return output

    def _resolve(self, ref: ColumnRef, scope: list[_BoundColumn]) -> _BoundColumn:
        matches = [
            col for col in scope
            if col.schema.name == ref.column and (ref.table is None or ref.table in col.qualifiers)
        ]
        name = f"{ref.table}.{ref.column}" if ref.table else ref.column
        if not matches:
            raise SemanticError(f"Kolom '{name}' tidak ditemukan dalam scope query", name)
        if len(matches) != 1:
            raise SemanticError(f"Kolom '{name}' ambigu", name)
        ref.table = matches[0].table
        return matches[0]

    def _bind_condition(
        self, expr: Expression, scope: list[_BoundColumn], active: set[int] | None = None,
    ) -> None:
        if active is None:
            active = set()
        if id(expr) in active:
            raise SemanticError("Pohon ekspresi mengandung siklus")
        active.add(id(expr))
        try:
            if isinstance(expr, Comparison) and isinstance(expr.op, CompOp):
                left = self._bind_operand(expr.left, scope, active)
                right = self._bind_operand(expr.right, scope, active)
                if not _compatible(left, right):
                    raise SemanticError("Tipe operand perbandingan tidak kompatibel")
            elif isinstance(expr, And) and expr.conditions:
                for condition in expr.conditions:
                    self._bind_condition(condition, scope, active)
            else:
                raise SemanticError("Kondisi harus berupa perbandingan atau AND")
        finally:
            active.remove(id(expr))

    def _bind_value(
        self, expr: Expression, scope: list[_BoundColumn], active: set[int] | None = None,
    ) -> DataType:
        if active is None:
            active = set()
        if id(expr) in active:
            raise SemanticError("Pohon ekspresi mengandung siklus")
        active.add(id(expr))
        try:
            if isinstance(expr, ColumnRef):
                return self._resolve(expr, scope).schema.type
            if isinstance(expr, Literal):
                if not isinstance(expr.type, DataType):
                    raise SemanticError("Tipe literal tidak dikenal")
                if expr.value is not None:
                    expected = (int,) if expr.type is DataType.INTEGER else (int, float) if expr.type is DataType.FLOAT else (str,)
                    if type(expr.value) not in expected:
                        raise SemanticError("Nilai literal tidak sesuai tipe yang dinyatakan")
                    if type(expr.value) is float and not isfinite(expr.value):
                        raise SemanticError("Nilai FLOAT harus berhingga")
                return expr.type
            if isinstance(expr, BinaryOp) and isinstance(expr.op, ArithOp):
                left = self._bind_value(expr.left, scope, active)
                right = self._bind_value(expr.right, scope, active)
                if left not in _NUMERIC or right not in _NUMERIC:
                    raise SemanticError("Operasi aritmetika hanya menerima angka")
                return DataType.FLOAT if DataType.FLOAT in (left, right) else DataType.INTEGER
            if isinstance(expr, Subquery):
                raise SemanticError("Subquery hanya boleh menjadi operand langsung perbandingan di kondisi")
            raise SemanticError(f"Ekspresi '{type(expr).__name__}' tidak didukung")
        finally:
            active.remove(id(expr))

    def _bind_operand(
        self, expr: Expression, scope: list[_BoundColumn], active: set[int],
    ) -> DataType:
        """Operand perbandingan: ekspresi biasa atau subquery skalar."""
        if not isinstance(expr, Subquery):
            return self._bind_value(expr, scope, active)
        if type(expr.plan) not in _ROW_NODES:
            raise SemanticError("Subquery harus berupa query SELECT")
        # scope luar tidak diteruskan karena subquery tidak boleh berkorelasi
        output = self._bind_plan(expr.plan, {}, set())
        if len(output) != 1:
            raise SemanticError("Subquery di kondisi harus menghasilkan tepat satu kolom")
        return output[0].schema.type

    def _subquery_scan_columns(self, node: SubqueryScan, inner: list[_BoundColumn]) -> list[_BoundColumn]:
        """Kolom keluaran subquery di FROM, diberi prefix alias subquery."""
        alias = node.alias
        if not isinstance(alias, str) or not alias.strip():
            raise SemanticError("Subquery di FROM wajib memakai alias")
        names = output_names(node.children[0], [col.schema.name for col in inner])
        for name in names:
            if names.count(name) > 1:
                raise SemanticError(
                    f"Kolom '{name}' muncul lebih dari sekali di subquery '{alias}', beri alias berbeda", name,
                )
        return [_BoundColumn(alias, replace(col.schema, name=name), (alias,)) for col, name in zip(inner, names)]

    def _check_aliases(self, node: Project) -> None:
        if not node.aliases:
            return
        if len(node.aliases) != len(node.columns):
            raise SemanticError("Jumlah alias kolom tidak sesuai jumlah kolom")
        for alias in node.aliases:
            if alias is not None and (not isinstance(alias, str) or not alias.strip()):
                raise SemanticError("Alias kolom tidak valid")

    def _table_exists(self, table: str) -> bool:
        assert self._catalog is not None
        try:
            self._catalog.get_schema(table)
        except SemanticError:
            return False
        return True

    def _bind_create_table(self, schema: TableSchema, schemas: dict[str, TableSchema]) -> None:
        """Validasi skema CREATE TABLE. Kolom primary key dijadikan NOT NULL."""
        name = schema.name
        if not isinstance(name, str) or not name.strip():
            raise SemanticError("Nama tabel tidak valid")
        if self._table_exists(name):
            raise SemanticError(f"Tabel '{name}' sudah ada", name)
        if not schema.columns:
            raise SemanticError("Tabel minimal punya satu kolom", name)

        names: list[str] = []
        for col in schema.columns:
            if not isinstance(col.name, str) or not col.name.strip():
                raise SemanticError("Nama kolom tidak valid", name)
            if col.name in names:
                raise SemanticError(f"Kolom '{col.name}' didefinisikan lebih dari sekali", col.name)
            names.append(col.name)
            if not isinstance(col.type, DataType):
                raise SemanticError(f"Tipe kolom '{col.name}' tidak dikenal", col.name)
            if col.type in _TEXT:
                if type(col.length) is not int or col.length <= 0:
                    raise SemanticError(f"Panjang kolom '{col.name}' harus bilangan bulat positif", col.name)
            elif col.length is not None:
                raise SemanticError(f"Panjang hanya berlaku untuk CHAR/VARCHAR ('{col.name}')", col.name)

        pk = schema.primary_key
        for key in pk:
            if key not in names:
                raise SemanticError(f"Kolom primary key '{key}' tidak ada di tabel", key)
            if pk.count(key) > 1:
                raise SemanticError(f"Kolom primary key '{key}' ditulis lebih dari sekali", key)
        schema.columns = [replace(col, nullable=False) if col.name in pk else col for col in schema.columns]

        by_name = {col.name: col for col in schema.columns}
        for fk in schema.foreign_keys:
            if fk.column not in by_name:
                raise SemanticError(f"Kolom foreign key '{fk.column}' tidak ada di tabel", fk.column)
            target = schema if fk.ref_table == name else self._schema(fk.ref_table, schemas)
            ref = next((col for col in target.columns if col.name == fk.ref_column), None)
            if ref is None:
                ref_name = f"{fk.ref_table}.{fk.ref_column}"
                raise SemanticError(f"Kolom rujukan '{ref_name}' tidak ditemukan", ref_name)
            if ref.type is not by_name[fk.column].type:
                raise SemanticError(
                    f"Tipe kolom foreign key '{fk.column}' tidak sama dengan kolom rujukan", fk.column,
                )


def bind_query(query: ParsedQuery, catalog: StorageManager | None = None) -> ParsedQuery:
    """Shortcut Binder(catalog).bind(query) (query data wajib memiliki katalog)"""
    return Binder(catalog).bind(query)
