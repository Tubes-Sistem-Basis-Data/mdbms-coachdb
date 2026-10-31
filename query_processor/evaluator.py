from __future__ import annotations

from common.expression import Expression
from query_processor.tuple import ExecRow, Value


class ExpressionEvaluator:
    def eval(self, expr: Expression, row: ExecRow) -> Value | bool:
        raise NotImplementedError("QP-04: eval belum diimplementasikan")

    def eval_bool(self, expr: Expression, row: ExecRow) -> bool:
        raise NotImplementedError("QP-04: eval_bool belum diimplementasikan")
