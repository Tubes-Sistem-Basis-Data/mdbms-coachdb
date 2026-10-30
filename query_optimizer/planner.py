"""Purpose: build a plan. Mock: return a dummy table scan."""

from __future__ import annotations

from common.plan import ParsedQuery, PlanNode, Project, QueryKind, TableScan


def plan_query(parsed: ParsedQuery | None) -> PlanNode | None:
    """Purpose: create an execution plan. Mock: always return a student scan."""
    if parsed is None:
        return None
    if parsed.kind is QueryKind.SELECT:
        return Project(columns=[], children=[TableScan(table="student")], output_columns=[])
    return None
