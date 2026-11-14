from __future__ import annotations

from dataclasses import replace
from math import isfinite

from common.catalog import DataType, IndexInfo, IndexKind
from common.errors import SemanticError
from common.expression import (
    And, ArithOp, BinaryOp, ColumnRef, Comparison, CompOp, Expression, Literal,
)
from common.plan import (
    CreateIndex, Delete, Filter, IndexScan, Insert, Join, JoinKind, ParsedQuery,
    PlanNode, Project, QueryKind, Sort, TableScan, Update,
)
from query_processor.context import ExecContext
from query_processor.errors import PlanError
from query_processor.evaluator import ExpressionEvaluator
from query_processor.operators.base import Executable, RowOperator
from query_processor.operators.create_index import CreateIndexExecution
from query_processor.operators.delete import DeleteExecution
from query_processor.operators.filter import FilterOp
from query_processor.operators.insert import InsertExecution
from query_processor.operators.join import BlockNestedLoopJoinOp
from query_processor.operators.project import ProjectOp, SelectExecution
from query_processor.operators.scan import IndexScanOp, TableScanOp
from query_processor.operators.sort import SortOp
from query_processor.operators.update import UpdateExecution
from query_processor.tuple import ColumnBinding, OutputSchema


_ARITY = {
    TableScan: 0, IndexScan: 0, Filter: 1, Project: 1, Join: 2, Sort: 1,
    Insert: 0, Update: 1, Delete: 1, CreateIndex: 0,
}
_ROW_NODES = (TableScan, IndexScan, Filter, Project, Join, Sort)
_ROOT_TYPES = {
    QueryKind.SELECT: Project,
    QueryKind.INSERT: Insert,
    QueryKind.UPDATE: Update,
    QueryKind.DELETE: Delete,
    QueryKind.CREATE_INDEX: CreateIndex,
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PlanError(f"Plan tidak valid: {message}")


def _name(value: object, label: str) -> None:
    _require(isinstance(value, str) and bool(value.strip()), f"{label} wajib terisi")


class PlanTranslator:
    """Validasi plan dan bangun operator tanpa eksekusi."""

    def build(self, parsed: ParsedQuery, ctx: ExecContext) -> Executable:
        """Bangun executable untuk SELECT, DML, atau index."""
        _require(isinstance(parsed, ParsedQuery), "input harus berupa ParsedQuery")
        _require(isinstance(parsed.kind, QueryKind), "jenis query tidak dikenal")
        expected = _ROOT_TYPES.get(parsed.kind)
        _require(expected is not None, "transaksi dan EXPLAIN harus ditangani facade QP")
        _require(type(parsed.plan) is expected, f"akar {parsed.kind.value} harus {expected.__name__}")
        _require(parsed.command is None, "query data tidak boleh membawa perintah transaksi")
        self.validate(parsed.plan)
        schemas: dict[str, OutputSchema] = {}
        node = parsed.plan
        if isinstance(node, Project):
            return SelectExecution(self._build_rows(node, ctx, schemas))
        if isinstance(node, Insert):
            schema = self._table_schema(node.table, ctx, schemas)
            _require(len(node.values) == len(schema.columns), "jumlah nilai INSERT tidak sesuai skema tabel")
            return InsertExecution(node.table, tuple(value.value for value in node.values))
        if isinstance(node, Update):
            source = self._build_rows(node.children[0], ctx, schemas)
            source.schema.index_of(ColumnRef(node.table, node.column))
            self._check_refs(node.expression, source.schema)
            return UpdateExecution(node.table, node.column, node.expression, source)
        if isinstance(node, Delete):
            return DeleteExecution(node.table, self._build_rows(node.children[0], ctx, schemas))
        schema = self._table_schema(node.index.table, ctx, schemas)
        schema.index_of(ColumnRef(node.index.table, node.index.column))
        return CreateIndexExecution(node.index)

    def build_rows(self, node: PlanNode, ctx: ExecContext) -> RowOperator:
        """Bangun subtree operator baca."""
        self.validate(node)
        _require(type(node) in _ROW_NODES, "subtree baris tidak boleh berupa perintah tulis")
        return self._build_rows(node, ctx, {})

    def validate(self, node: PlanNode) -> None:
        """Validasi struktur dan parameter plan."""
        try:
            self._validate_node(node, set())
        except RecursionError as exc:
            raise PlanError("Plan tidak valid: pohon plan atau ekspresi terlalu dalam") from exc

    def _validate_node(self, node: PlanNode, active: set[int]) -> None:
        node_type = type(node)
        _require(node_type in _ARITY, f"node '{node_type.__name__}' tidak didukung")
        _require(id(node) not in active, "pohon plan mengandung siklus")
        _require(isinstance(node.children, list), f"children {node_type.__name__} harus berupa list")
        _require(len(node.children) == _ARITY[node_type],
                 f"{node_type.__name__} membutuhkan {_ARITY[node_type]} anak")
        active.add(id(node))
        try:
            for child in node.children:
                self._validate_node(child, active)
                _require(type(child) in _ROW_NODES, "anak operator harus menghasilkan baris")
            if isinstance(node, (TableScan, IndexScan, Insert, Update, Delete)):
                _name(node.table, "nama tabel")
            if isinstance(node, IndexScan):
                self._validate_index(node.index)
                _require(node.index.table == node.table, "tabel IndexScan berbeda dengan tabel index")
                _require(isinstance(node.op, CompOp) and node.op is not CompOp.NE,
                         "IndexScan hanya mendukung =, >, >=, <, <=")
                _require(node.index.kind is not IndexKind.HASH or node.op is CompOp.EQ,
                         "index HASH hanya mendukung pencarian kesamaan")
                for value in (node.key, node.low, node.high):
                    self._validate_value(value)
                if node.op is CompOp.EQ:
                    _require(node.low is None and node.high is None, "IndexScan = memakai key, bukan low/high")
                else:
                    _require(node.key is None, "IndexScan rentang memakai low/high, bukan key")
                    bound = node.low if node.op in (CompOp.GT, CompOp.GE) else node.high
                    _require(bound is not None, "batas rentang IndexScan wajib terisi")
            elif isinstance(node, Filter):
                self._validate_expr(node.condition, predicate=True)
            elif isinstance(node, Project):
                _require(isinstance(node.columns, list) and bool(node.columns),
                         "Project.columns wajib berisi ColumnRef; SELECT * harus diekspansi QO")
                for ref in node.columns:
                    self._validate_ref(ref)
            elif isinstance(node, Join):
                _require(isinstance(node.kind, JoinKind), "jenis join tidak dikenal")
                _require(node.algorithm == "BNLJ", "algoritma join yang didukung hanya BNLJ")
                if node.kind is JoinKind.THETA:
                    self._validate_expr(node.condition, predicate=True)
                else:
                    _require(node.condition is None, "join CROSS/NATURAL tidak menerima kondisi ON")
            elif isinstance(node, Sort):
                self._validate_ref(node.column)
                _require(type(node.ascending) is bool, "Sort.ascending harus boolean")
            elif isinstance(node, Insert):
                _require(isinstance(node.values, list) and bool(node.values), "nilai INSERT wajib terisi")
                for value in node.values:
                    _require(type(value) is Literal, "INSERT hanya menerima Literal")
                    self._validate_expr(value)
            elif isinstance(node, (Update, Delete)):
                self._validate_target(node.children[0], node.table)
                if isinstance(node, Update):
                    _name(node.column, "kolom UPDATE")
                    self._validate_expr(node.expression)
            elif isinstance(node, CreateIndex):
                self._validate_index(node.index)
        finally:
            active.remove(id(node))

    def _validate_target(self, node: PlanNode, table: str) -> None:
        while isinstance(node, Filter):
            node = node.children[0]
        _require(type(node) in (TableScan, IndexScan),
                 "sumber UPDATE/DELETE harus Scan/IndexScan dengan Filter opsional agar RID tetap sah")
        _require(node.table == table, "sumber UPDATE/DELETE harus berasal dari tabel target")

    def _validate_index(self, index: IndexInfo) -> None:
        _require(isinstance(index, IndexInfo), "metadata index harus berupa IndexInfo")
        for value, label in ((index.name, "nama index"), (index.table, "tabel index"),
                             (index.column, "kolom index")):
            _name(value, label)
        _require(isinstance(index.kind, IndexKind), "jenis index tidak dikenal")

    def _validate_ref(self, ref: ColumnRef) -> None:
        _require(type(ref) is ColumnRef, "referensi kolom harus berupa ColumnRef")
        _name(ref.column, "nama kolom")
        if ref.table is not None:
            _name(ref.table, "prefix tabel")
        _require(ref.column != "*", "SELECT * harus diekspansi binder QO")

    def _validate_value(self, value: object) -> None:
        _require(value is None or type(value) in (int, float, str), "nilai harus INTEGER/FLOAT/teks/NULL")
        if type(value) is float:
            _require(isfinite(value), "nilai FLOAT harus berhingga")

    def _validate_expr(
        self, expr: Expression, predicate: bool = False, active: set[int] | None = None,
    ) -> None:
        if active is None:
            active = set()
        _require(id(expr) not in active, "pohon ekspresi mengandung siklus")
        allowed = (Comparison, And) if predicate else (ColumnRef, Literal, BinaryOp)
        _require(type(expr) in allowed, "bentuk kondisi/ekspresi tidak didukung")
        active.add(id(expr))
        try:
            if isinstance(expr, ColumnRef):
                self._validate_ref(expr)
            elif isinstance(expr, Literal):
                _require(isinstance(expr.type, DataType), "tipe Literal tidak dikenal")
                self._validate_value(expr.value)
            elif isinstance(expr, (BinaryOp, Comparison)):
                op_type = ArithOp if isinstance(expr, BinaryOp) else CompOp
                _require(isinstance(expr.op, op_type), "operator ekspresi tidak dikenal")
                self._validate_expr(expr.left, active=active)
                self._validate_expr(expr.right, active=active)
            else:
                _require(isinstance(expr.conditions, list) and bool(expr.conditions), "AND wajib berisi kondisi")
                for condition in expr.conditions:
                    self._validate_expr(condition, predicate=True, active=active)
        finally:
            active.remove(id(expr))

    def _table_schema(
        self, table: str, ctx: ExecContext, schemas: dict[str, OutputSchema],
    ) -> OutputSchema:
        if table not in schemas:
            try:
                source = ctx.sm.get_schema(table)
            except SemanticError as exc:
                raise PlanError(f"Plan tidak valid: tabel '{table}' tidak tersedia: {exc.message}") from exc
            _require(source.name == table and bool(source.columns), f"skema tabel '{table}' tidak valid/kosong")
            schemas[table] = OutputSchema([
                ColumnBinding(table, column.name, column.type) for column in source.columns
            ])
        return OutputSchema(list(schemas[table].columns))

    def _check_refs(self, expr: Expression, schema: OutputSchema) -> None:
        if isinstance(expr, ColumnRef):
            schema.index_of(expr)
        elif isinstance(expr, (BinaryOp, Comparison)):
            self._check_refs(expr.left, schema)
            self._check_refs(expr.right, schema)
        elif isinstance(expr, And):
            for condition in expr.conditions:
                self._check_refs(condition, schema)

    def _build_rows(
        self, node: PlanNode, ctx: ExecContext, schemas: dict[str, OutputSchema],
    ) -> RowOperator:
        if isinstance(node, (TableScan, IndexScan)):
            schema = self._table_schema(node.table, ctx, schemas)
            if isinstance(node, IndexScan):
                schema.index_of(ColumnRef(node.table, node.index.column))
                return IndexScanOp(node.table, node.index, node.op, schema,
                                   key=node.key, low=node.low, high=node.high)
            return TableScanOp(node.table, schema)
        children = [self._build_rows(child, ctx, schemas) for child in node.children]
        if isinstance(node, Join):
            left, right = children
            combined = OutputSchema(left.schema.columns + right.schema.columns)
            if node.condition is not None:
                self._check_refs(node.condition, combined)
            schema, shared = self._join_schema(left.schema, right.schema, node.kind)
            return BlockNestedLoopJoinOp(left, right, schema, node.kind, node.condition,
                                         evaluator=ExpressionEvaluator(), shared_columns=shared)
        child = children[0]
        if isinstance(node, Filter):
            self._check_refs(node.condition, child.schema)
            return FilterOp(child, node.condition)
        if isinstance(node, Sort):
            child.schema.index_of(node.column)
            return SortOp(child, node.column, node.ascending)
        return ProjectOp(child, list(node.columns))

    def _join_schema(
        self, left: OutputSchema, right: OutputSchema, kind: JoinKind,
    ) -> tuple[OutputSchema, tuple[tuple[int, int], ...]]:
        if kind is not JoinKind.NATURAL:
            return OutputSchema(left.columns + right.columns), ()
        columns = list(left.columns)
        shared: list[tuple[int, int]] = []
        for right_pos, right_column in enumerate(right.columns):
            matches = [i for i, column in enumerate(left.columns) if column.name == right_column.name]
            if not matches:
                columns.append(right_column)
                continue
            _require(len(matches) == 1, f"kolom NATURAL JOIN '{right_column.name}' ambigu di kiri")
            _require(sum(c.name == right_column.name for c in right.columns) == 1,
                     f"kolom NATURAL JOIN '{right_column.name}' ambigu di kanan")
            left_pos = matches[0]
            original = columns[left_pos]
            numeric = (DataType.INTEGER, DataType.FLOAT)
            text = (DataType.CHAR, DataType.VARCHAR)
            _require((original.type in numeric and right_column.type in numeric)
                     or (original.type in text and right_column.type in text),
                     f"tipe kolom NATURAL JOIN '{right_column.name}' tidak kompatibel")
            qualifiers = dict.fromkeys(original.alternate_qualifiers
                                       + (right_column.qualifier,) + right_column.alternate_qualifiers)
            columns[left_pos] = replace(original, alternate_qualifiers=tuple(qualifiers))
            shared.append((left_pos, right_pos))
        return OutputSchema(columns), tuple(shared)
