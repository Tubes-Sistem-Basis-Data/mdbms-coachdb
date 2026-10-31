from __future__ import annotations

from common.plan import ParsedQuery, PlanNode
from query_processor.context import ExecContext
from query_processor.operators.base import Executable, RowOperator


class PlanTranslator:
    def build(self, parsed: ParsedQuery, ctx: ExecContext) -> Executable:
        raise NotImplementedError("QP-03: build belum diimplementasikan")

    def build_rows(self, node: PlanNode, ctx: ExecContext) -> RowOperator:
        raise NotImplementedError("QP-03: build_rows belum diimplementasikan")

    def validate(self, node: PlanNode) -> None:
        raise NotImplementedError("QP-03: validate belum diimplementasikan")
