"""Mock Query Optimizer: hanya mengenali bentuk minimal lewat regex."""

from __future__ import annotations

import dataclasses
import re

from common.errors import QuerySyntaxError
from common.expression import ColumnRef
from common.interfaces import QueryOptimizer, StorageManager
from common.plan import (
    ParsedQuery,
    PlanNode,
    Project,
    QueryKind,
    TableScan,
    TransactionCommand,
)

_BEGIN = re.compile(r"^BEGIN(\s+TRANSACTION)?$", re.IGNORECASE)
_COMMIT = re.compile(r"^COMMIT$", re.IGNORECASE)
_ABORT = re.compile(r"^ABORT$", re.IGNORECASE)
_SELECT_ALL = re.compile(r"^SELECT\s+\*\s+FROM\s+([A-Za-z_][A-Za-z0-9_]*)$", re.IGNORECASE)
_EXPLAIN = re.compile(r"^EXPLAIN\s+(.+)$", re.IGNORECASE | re.DOTALL)


class MockQueryOptimizer(QueryOptimizer):
    """QO mock. Plan SELECT selalu Project(TableScan), optimasi tidak mengubah plan."""

    def __init__(self, sm: StorageManager) -> None:
        self._sm = sm

    def _parse_select(self, sql: str, body: str) -> ParsedQuery | None:
        m = _SELECT_ALL.match(body)
        if not m:
            return None
        table = m.group(1).lower()
        schema = self._sm.get_schema(table)
        cols = [ColumnRef(table, c) for c in schema.column_names()]
        scan = TableScan(table=table, output_columns=list(cols))
        plan = Project(columns=list(cols), children=[scan], output_columns=list(cols))
        return ParsedQuery(sql=sql, kind=QueryKind.SELECT, plan=plan)

    def parse_query(self, sql: str) -> ParsedQuery:
        """Kenali BEGIN, COMMIT, ABORT, SELECT * FROM t, dan EXPLAIN SELECT * FROM t."""
        body = sql.strip().rstrip(";").strip()
        if _BEGIN.match(body):
            return ParsedQuery(sql, QueryKind.TRANSACTION, command=TransactionCommand.BEGIN)
        if _COMMIT.match(body):
            return ParsedQuery(sql, QueryKind.TRANSACTION, command=TransactionCommand.COMMIT)
        if _ABORT.match(body):
            return ParsedQuery(sql, QueryKind.TRANSACTION, command=TransactionCommand.ABORT)
        m = _EXPLAIN.match(body)
        if m:
            inner = self._parse_select(sql, m.group(1).strip())
            if inner is not None:
                return ParsedQuery(sql, QueryKind.EXPLAIN, plan=inner.plan)
        else:
            parsed = self._parse_select(sql, body)
            if parsed is not None:
                return parsed
        token = body.split()[0] if body.split() else ""
        raise QuerySyntaxError("mock: belum didukung", position=0, token=token)

    def optimize_query(self, query: ParsedQuery) -> ParsedQuery:
        """Plan tidak diubah, hanya biaya yang diisi."""
        if query.plan is None:
            return query
        cost = self.get_cost(query.plan)
        query.plan.estimated_cost = cost
        return dataclasses.replace(query, estimated_cost=cost)

    def get_cost(self, plan: PlanNode) -> float:
        """Jumlah b_r tabel yang di-scan."""
        cost = float(self._sm.get_stats(plan.table).b_r) if isinstance(plan, TableScan) else 0.0
        return cost + sum(self.get_cost(c) for c in plan.children)

    def explain(self, query: ParsedQuery) -> str:
        """Cetak pohon plan beserta biaya. Plan sebelum dan sesudah sama di mock."""
        if query.plan is None:
            return f"{query.kind.value}: tidak ada plan"
        tree = "\n".join(self._render(query.plan, 0))
        cost = self.get_cost(query.plan)
        return f"Plan awal:\n{tree}\nPlan optimal:\n{tree}\nBiaya: {cost:g} blok"

    def _render(self, node: PlanNode, depth: int) -> list[str]:
        match node:
            case TableScan(table=t):
                label = f"TableScan({t})"
            case Project(columns=cols):
                label = "Project(" + ", ".join(f"{c.table}.{c.column}" for c in cols) + ")"
            case _:
                label = type(node).__name__
        lines = ["  " * depth + label]
        for child in node.children:
            lines.extend(self._render(child, depth + 1))
        return lines
