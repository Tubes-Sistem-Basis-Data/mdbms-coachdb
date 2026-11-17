"""Bentuk akar SELECT yang dipakai translator dan runner scalar."""

from common.plan import Limit, PlanNode, Project
from query_processor.errors import PlanError


def select_project(node: PlanNode) -> Project:
    """Ambil Project di balik rantai LIMIT tanpa membaca data."""
    active: set[int] = set()
    while type(node) is Limit:
        if id(node) in active:
            raise PlanError("Plan tidak valid: pohon SELECT mengandung siklus")
        active.add(id(node))
        if type(node.count) is not int or node.count < 0:
            raise PlanError("Plan tidak valid: Limit.count harus bilangan bulat nonnegatif")
        if not isinstance(node.children, list) or len(node.children) != 1:
            raise PlanError("Plan tidak valid: Limit membutuhkan satu anak")
        node = node.children[0]
    if type(node) is not Project:
        raise PlanError("Plan SELECT harus berupa Project atau rantai Limit yang membungkus Project")
    return node
