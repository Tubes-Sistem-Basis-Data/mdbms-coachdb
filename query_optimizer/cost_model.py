from __future__ import annotations

import operator
from dataclasses import dataclass, replace
from math import ceil, floor, isfinite, prod

from common import config
from common.catalog import DataType, TableSchema, TableStats
from common.errors import SemanticError
from common.expression import And, BinaryOp, ColumnRef, Comparison, CompOp, Expression, Literal, Subquery
from common.interfaces import StorageManager
from common.plan import (
    CreateTable, DropTable, Filter, IndexScan, Join, JoinKind, Limit, PlanNode, Project, Sort,
    SubqueryScan, TableScan,
)
from query_optimizer.plan_utils import output_names


_ARITY = {
    PlanNode: 0, TableScan: 0, IndexScan: 0, Filter: 1, Project: 1, Join: 2, Sort: 1,
    Limit: 1, SubqueryScan: 1, CreateTable: 0, DropTable: 0,
}
_UNKNOWN_EQUALITY = 0.1
_RANGE_SELECTIVITY = 1 / 3
_COMPARISONS = {
    CompOp.EQ: operator.eq, CompOp.NE: operator.ne,
    CompOp.GT: operator.gt, CompOp.GE: operator.ge,
    CompOp.LT: operator.lt, CompOp.LE: operator.le,
}


@dataclass(frozen=True)
class _Column:
    table: str
    name: str
    width: float
    distinct: float | None
    qualifiers: tuple[str, ...]


@dataclass(frozen=True)
class _Estimate:
    rows: float
    blocks: int
    width: float
    columns: list[_Column]
    cost: float


def _resolve(ref: ColumnRef, columns: list[_Column]) -> _Column:
    matches = [col for col in columns if col.name == ref.column and (ref.table is None or ref.table in col.qualifiers)]
    name = f"{ref.table}.{ref.column}" if ref.table else ref.column
    if not matches or len(matches) > 1:
        raise SemanticError(f"Kolom '{name}' tidak ditemukan atau ambigu pada cost model", name)
    return matches[0]


def _cap_distinct(columns: list[_Column], rows: float) -> list[_Column]:
    return [replace(col, distinct=0.0 if rows == 0 else min(col.distinct, rows) if col.distinct is not None else None)
            for col in columns]


class CostModel:
    """Model biaya query plan berdasarkan statistik SM (QO-08)."""

    def __init__(
        self, catalog: StorageManager | None = None, *,
        buffer_frames: int | None = None, block_size: int | None = None,
    ) -> None:
        self._catalog = catalog
        self._block_size = config.BLOCK_SIZE if block_size is None else block_size
        self._buffer_frames = config.BUFFER_FRAMES if buffer_frames is None else buffer_frames
        if type(self._block_size) is not int or self._block_size <= 0:
            raise ValueError("block_size harus integer positif")
        if type(self._buffer_frames) is not int or self._buffer_frames < 3:
            raise ValueError("buffer_frames harus integer minimal 3 untuk BNLJ/merge sort")

    def get_cost(self, plan: PlanNode) -> float:
        """Hitung ulang total biaya subtree dan anotasi setiap node.

        Katalog hanya dicache dalam pemanggilan ini. Anotasi estimasi lama tidak
        dipakai sebagai input sehingga aman membandingkan plan yang diubah.
        """
        if self._catalog is None:
            raise SemanticError("CostModel membutuhkan katalog StorageManager")
        try:
            return self._estimate(plan, {}, set()).cost
        except RecursionError as exc:
            raise SemanticError("Pohon plan atau ekspresi terlalu dalam") from exc

    def estimate(self, plan: PlanNode) -> float:
        """Biaya total subtree, sama dengan get_cost (bukan biaya lokal node)."""
        return self.get_cost(plan)

    def _blocks(self, rows: float, width: float) -> int:
        if rows == 0:
            return 0
        capacity = max(1, floor(self._block_size / max(1.0, width)))
        return ceil(rows / capacity)

    def _estimate(
        self, node: PlanNode, catalog: dict[str, tuple[TableStats, TableSchema]], active: set[int],
    ) -> _Estimate:
        arity = _ARITY.get(type(node))
        if arity is None:
            raise SemanticError(f"Estimasi {type(node).__name__} belum didukung pada cost model M2")
        if id(node) in active:
            raise SemanticError("Pohon plan mengandung siklus")
        if len(node.children) != arity:
            raise SemanticError(f"{type(node).__name__} membutuhkan {arity} anak")
        active.add(id(node))
        try:
            children = [self._estimate(child, catalog, active) for child in node.children]
            if type(node) is PlanNode or isinstance(node, (CreateTable, DropTable)):
                # Sentinel plan kosong yang dipakai uji kontrak facade, dan DDL tanpa transfer blok data.
                result = _Estimate(0.0, 0, 0.0, [], 0.0)
            elif isinstance(node, TableScan):
                result = self._scan(node.table, catalog)
                if node.alias is not None:
                    result = self._rename(result, node.alias, [col.name for col in result.columns])
            elif isinstance(node, IndexScan):
                result = self._index_scan(node, self._scan(node.table, catalog))
                if node.alias is not None:
                    result = self._rename(result, node.alias, [col.name for col in result.columns])
            elif isinstance(node, Filter):
                result = self._filter(node.condition, children[0])
                result = replace(result, cost=result.cost + self._subquery_cost(node.condition, catalog, active))
            elif isinstance(node, Join):
                result = self._join(node, children[0], children[1])
                if node.condition is not None:
                    result = replace(result, cost=result.cost + self._subquery_cost(node.condition, catalog, active))
            elif isinstance(node, Limit):
                child = children[0]
                if type(node.count) is not int or node.count < 0:
                    raise SemanticError("Nilai LIMIT harus bilangan bulat tidak negatif")
                rows = min(child.rows, float(node.count))
                # biaya tetap biaya anak karena operator di bawahnya bisa saja harus selesai lebih dulu (mis. Sort)
                result = replace(child, rows=rows, blocks=self._blocks(rows, child.width),
                                 columns=_cap_distinct(child.columns, rows))
            elif isinstance(node, SubqueryScan):
                child = children[0]
                names = output_names(node.children[0], [col.name for col in child.columns])
                result = self._rename(child, node.alias, names)
            elif isinstance(node, Sort):
                child = children[0]
                _resolve(node.column, child.columns)
                passes = 0
                runs = ceil(child.blocks / self._buffer_frames) if child.rows else 0
                while runs > 1:
                    runs = ceil(runs / (self._buffer_frames - 1))
                    passes += 1
                result = replace(child, cost=child.cost + 2 * child.blocks * passes)
            else:
                child = children[0]
                columns = [_resolve(ref, child.columns) for ref in node.columns] if node.columns else child.columns
                width = sum(col.width for col in columns)
                result = replace(child, columns=columns, width=width, blocks=self._blocks(child.rows, width))
            node.estimated_rows = result.rows
            node.estimated_cost = result.cost
            return result
        finally:
            active.remove(id(node))

    def _rename(self, estimate: _Estimate, source: str, names: list[str]) -> _Estimate:
        """Ganti prefix kolom menjadi alias tabel atau alias subquery."""
        columns = [replace(col, table=source, name=name, qualifiers=(source,))
                   for col, name in zip(estimate.columns, names)]
        return replace(estimate, columns=columns)

    def _subquery_cost(
        self, expr: Expression, catalog: dict[str, tuple[TableStats, TableSchema]], active: set[int],
    ) -> float:
        """Biaya subquery skalar di kondisi. Subquery tak-berkorelasi cukup dievaluasi sekali."""
        if isinstance(expr, Subquery):
            return self._estimate(expr.plan, catalog, active).cost
        if isinstance(expr, And):
            return sum(self._subquery_cost(cond, catalog, active) for cond in expr.conditions)
        if isinstance(expr, (Comparison, BinaryOp)):
            return self._subquery_cost(expr.left, catalog, active) + self._subquery_cost(expr.right, catalog, active)
        return 0.0

    def _index_scan(self, node: IndexScan, scan: _Estimate) -> _Estimate:
        """Kompatibilitas QO-12; biaya tetap full scan sampai model index M3."""
        if node.index.table != node.table:
            raise SemanticError("Tabel IndexScan berbeda dengan metadata index", node.table)
        ref = ColumnRef(node.table, node.index.column)
        _resolve(ref, scan.columns)

        def comparison(op: CompOp, value: object) -> Comparison:
            value_type = {int: DataType.INTEGER, float: DataType.FLOAT, str: DataType.VARCHAR, type(None): DataType.INTEGER}.get(type(value))
            if value_type is None:
                raise SemanticError("Tipe kunci IndexScan tidak valid")
            return Comparison(op, ref, Literal(value, value_type))

        if node.op is CompOp.EQ:
            if node.low is not None or node.high is not None:
                raise SemanticError("IndexScan kesamaan memakai key, bukan low/high")
            condition = comparison(CompOp.EQ, node.key)
        elif node.op in (CompOp.GT, CompOp.GE, CompOp.LT, CompOp.LE):
            lower = node.op in (CompOp.GT, CompOp.GE)
            if node.key is not None or (node.low if lower else node.high) is None:
                raise SemanticError("IndexScan rentang membutuhkan batas low/high yang sesuai")
            conditions = []
            if node.low is not None:
                conditions.append(comparison(node.op if lower else CompOp.GE, node.low))
            if node.high is not None:
                conditions.append(comparison(node.op if not lower else CompOp.LE, node.high))
            condition = conditions[0] if len(conditions) == 1 else And(conditions)
        else:
            raise SemanticError("Operator IndexScan tidak didukung")
        # Selektivitas memakai kolom dan predikat index, bukan jumlah kolom tabel.
        # Jangan mengasumsikan penghematan I/O tanpa model traversal/fetch index.
        return self._filter(condition, scan)

    def _join(self, node: Join, left: _Estimate, right: _Estimate) -> _Estimate:
        if node.algorithm != "BNLJ":
            raise SemanticError("Cost model M2 hanya mendukung algoritma BNLJ")
        columns = left.columns + right.columns
        if node.kind is JoinKind.THETA:
            selectivity = self._selectivity(node.condition, columns, set())
            columns = self._equality_distinct(node.condition, columns)
        elif node.kind in (JoinKind.CROSS, JoinKind.NATURAL):
            if node.condition is not None:
                raise SemanticError("Join CROSS/NATURAL tidak menerima kondisi ON")
            selectivity = 1.0
            if node.kind is JoinKind.NATURAL:
                columns, selectivity = self._natural(left.columns, right.columns)
        else:
            raise SemanticError("Jenis join tidak dikenal")
        rows = left.rows * right.rows * selectivity
        width = sum(col.width for col in columns)
        chunks = ceil(left.blocks / (self._buffer_frames - 2)) if left.rows else 0
        # Biaya kanan sudah mencakup subtree-nya, jangan dijumlahkan sekali lagi.
        cost = left.cost + chunks * right.cost
        return _Estimate(rows, self._blocks(rows, width), width, _cap_distinct(columns, rows), cost)

    def _natural(self, left: list[_Column], right: list[_Column]) -> tuple[list[_Column], float]:
        columns = list(left)
        selectivity = 1.0
        for column in right:
            matches = [i for i, col in enumerate(left) if col.name == column.name]
            if not matches:
                columns.append(column)
                continue
            if len(matches) != 1 or sum(col.name == column.name for col in right) != 1:
                raise SemanticError(f"Kolom NATURAL JOIN '{column.name}' ambigu", column.name)
            pos = matches[0]
            original = columns[pos]
            condition = Comparison(CompOp.EQ, ColumnRef(original.table, original.name), ColumnRef(column.table, column.name))
            selectivity *= self._selectivity(condition, left + right, set())
            ndvs = [col.distinct for col in (original, column) if col.distinct is not None]
            columns[pos] = replace(original, qualifiers=original.qualifiers + column.qualifiers,
                                   distinct=min(ndvs) if ndvs else None)
        return columns, selectivity

    def _scan(self, table: str, catalog: dict[str, tuple[TableStats, TableSchema]]) -> _Estimate:
        if table not in catalog:
            assert self._catalog is not None
            stats = self._catalog.get_stats(table)
            schema = self._catalog.get_schema(table)
            if stats.table != table:
                raise SemanticError("Statistik berasal dari tabel yang berbeda", table)
            counts = (stats.n_r, stats.b_r, stats.f_r, *stats.distinct.values())
            if any(type(value) is not int or value < 0 for value in counts):
                raise SemanticError("Statistik jumlah baris/blok/distinct harus integer nonnegatif", table)
            if type(stats.l_r) not in (int, float) or not isfinite(stats.l_r) or stats.l_r < 0:
                raise SemanticError("Ukuran tuple pada statistik tidak valid", table)
            catalog[table] = stats, schema
        stats, schema = catalog[table]
        width = float(stats.l_r)
        if stats.n_r and width == 0:
            if stats.f_r == 0:
                raise SemanticError("Statistik tabel nonkosong membutuhkan l_r atau f_r", table)
            width = self._block_size / stats.f_r
        if stats.n_r and not schema.columns:
            raise SemanticError("Skema tabel nonkosong belum tersedia", table)
        columns = [
            _Column(table, col.name, width / len(schema.columns),
                    float(stats.distinct[col.name]) if stats.distinct.get(col.name, 0) > 0 else None, (table,))
            for col in schema.columns
        ]
        blocks = stats.b_r
        if stats.n_r and blocks == 0:
            blocks = ceil(stats.n_r / stats.f_r) if stats.f_r else self._blocks(stats.n_r, width)
        return _Estimate(float(stats.n_r), blocks, width, _cap_distinct(columns, stats.n_r), float(blocks))

    def _filter(self, condition: Expression, child: _Estimate) -> _Estimate:
        selectivity = self._selectivity(condition, child.columns, set())
        rows = child.rows * selectivity
        columns = self._equality_distinct(condition, child.columns)
        return replace(child, rows=rows, blocks=self._blocks(rows, child.width), columns=_cap_distinct(columns, rows))

    def _equality_distinct(self, condition: Expression, columns: list[_Column]) -> list[_Column]:
        # Kelompok equality harus transitif dan tidak bergantung urutan AND.
        parents = {(col.table, col.name): (col.table, col.name) for col in columns}

        def root(col: _Column) -> tuple[str, str]:
            key = (col.table, col.name)
            while parents[key] != key:
                parents[key] = parents[parents[key]]
                key = parents[key]
            return key

        literals: list[_Column] = []
        pending = [condition]
        while pending:
            expr = pending.pop()
            if isinstance(expr, And):
                pending.extend(expr.conditions)
            elif isinstance(expr, Comparison) and expr.op is CompOp.EQ:
                left = _resolve(expr.left, columns) if isinstance(expr.left, ColumnRef) else None
                right = _resolve(expr.right, columns) if isinstance(expr.right, ColumnRef) else None
                if left is not None and right is not None:
                    parents[root(right)] = root(left)
                elif left is not None and isinstance(expr.right, Literal):
                    literals.append(left)
                elif right is not None and isinstance(expr.left, Literal):
                    literals.append(right)
        minima: dict[tuple[str, str], float] = {}
        for col in columns:
            if col.distinct is not None:
                key = root(col)
                minima[key] = min(minima.get(key, col.distinct), col.distinct)
        for col in literals:
            key = root(col)
            minima[key] = min(minima.get(key, 1.0), 1.0)
        return [replace(col, distinct=minima.get(root(col), col.distinct)) for col in columns]

    def _selectivity(self, expr: Expression, columns: list[_Column], active: set[int]) -> float:
        if id(expr) in active:
            raise SemanticError("Pohon ekspresi mengandung siklus")
        active.add(id(expr))
        try:
            if isinstance(expr, And) and expr.conditions:
                return prod(self._selectivity(cond, columns, active) for cond in expr.conditions)
            if not isinstance(expr, Comparison) or not isinstance(expr.op, CompOp):
                raise SemanticError("Cost model memerlukan kondisi Comparison atau AND")
            left = self._value(expr.left, columns, active)
            right = self._value(expr.right, columns, active)
            if any(isinstance(value, Literal) and value.value is None for value in (expr.left, expr.right)):
                return 0.0
            if isinstance(expr.left, Literal) and isinstance(expr.right, Literal):
                left_value, right_value = expr.left.value, expr.right.value
                if DataType.CHAR in (expr.left.type, expr.right.type) and isinstance(left_value, str) and isinstance(right_value, str):
                    left_value, right_value = left_value.rstrip(" "), right_value.rstrip(" ")
                try:
                    return float(_COMPARISONS[expr.op](left_value, right_value))
                except TypeError as exc:
                    raise SemanticError("Tipe perbandingan literal tidak kompatibel") from exc
            if left is not None and right is not None and (left.table, left.name) == (right.table, right.name):
                return float(expr.op in (CompOp.EQ, CompOp.GE, CompOp.LE))
            if expr.op in (CompOp.EQ, CompOp.NE):
                ndv = [max(1.0, col.distinct) if col.distinct is not None else 1 / _UNKNOWN_EQUALITY
                       for col in (left, right) if col is not None]
                equality = 1 / max(ndv) if ndv else _UNKNOWN_EQUALITY
                return equality if expr.op is CompOp.EQ else 1 - equality
            return _RANGE_SELECTIVITY
        finally:
            active.remove(id(expr))

    def _value(self, expr: Expression, columns: list[_Column], active: set[int]) -> _Column | None:
        if id(expr) in active:
            raise SemanticError("Pohon ekspresi mengandung siklus")
        active.add(id(expr))
        try:
            if isinstance(expr, ColumnRef):
                return _resolve(expr, columns)
            if isinstance(expr, (Literal, Subquery)):
                return None
            if isinstance(expr, BinaryOp):
                self._value(expr.left, columns, active)
                self._value(expr.right, columns, active)
                return None
            raise SemanticError(f"Ekspresi '{type(expr).__name__}' tidak didukung cost model")
        finally:
            active.remove(id(expr))


def estimate_cost(
    plan: PlanNode, catalog: StorageManager | None = None, *,
    buffer_frames: int | None = None, block_size: int | None = None,
) -> float:
    """Hitung total biaya plan dengan katalog dan parameter fisik yang diberikan."""
    return CostModel(catalog, buffer_frames=buffer_frames, block_size=block_size).get_cost(plan)
