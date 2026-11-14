"""Tipe resolver subquery skalar untuk komponen internal QP."""

from typing import Callable

from common.catalog import DataType
from common.expression import Subquery
from query_processor.tuple import Value


ScalarValue = tuple[Value, DataType]
ScalarResolver = Callable[[Subquery], ScalarValue]
