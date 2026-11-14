"""Resolver subquery skalar dalam transaksi statement pemanggil."""

from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING, Callable

from common.catalog import DataType
from common.expression import Subquery
from common.plan import PlanNode, Project
from query_processor.errors import ExecutionError, PlanError
from query_processor.tuple import ExecRow, Value

if TYPE_CHECKING:
    from query_processor.context import ExecContext
    from query_processor.operators.base import RowOperator


ScalarValue = tuple[Value, DataType]
ScalarResolver = Callable[[Subquery], ScalarValue]


def run_scalar(
    subquery: Subquery,
    ctx: ExecContext,
    build_rows: Callable[[PlanNode, ExecContext], RowOperator],
) -> ScalarValue:
    if not isinstance(subquery.plan, Project):
        raise PlanError("Subquery skalar harus mempunyai plan SELECT")
    root = build_rows(subquery.plan, ctx)
    try:
        if len(root.schema.columns) != 1:
            raise PlanError("Subquery skalar harus menghasilkan tepat satu kolom")
        data_type = root.schema.columns[0].type
        if not isinstance(data_type, DataType):
            raise PlanError("Tipe kolom subquery skalar tidak valid")
        root.open(ctx)
        first = root.next()
        if first is None:
            result = (None, data_type)
        else:
            if (
                not isinstance(first, ExecRow)
                or not isinstance(first.values, tuple)
                or len(first.values) != 1
            ):
                raise ExecutionError("Baris subquery skalar tidak sesuai skema")
            if root.next() is not None:
                raise ExecutionError("Subquery skalar menghasilkan lebih dari satu baris")
            result = (first.values[0], data_type)
    except BaseException:
        with suppress(BaseException):
            root.close()
        raise
    root.close()
    return result
