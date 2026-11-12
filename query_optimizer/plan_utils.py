"""Fungsi bantu plan yang dipakai beberapa bagian QO."""

from __future__ import annotations

from common.plan import IndexScan, Limit, PlanNode, Project, SubqueryScan, TableScan


def source_name(node: PlanNode) -> str | None:
    """Nama sumber baris yang dipakai sebagai prefix kolom: alias jika ada, nama tabel jika tidak."""
    if isinstance(node, (TableScan, IndexScan)):
        return node.alias or node.table
    if isinstance(node, SubqueryScan):
        return node.alias
    return None


def source_list(node: PlanNode) -> list[str]:
    """Nama sumber di subtree, urut kiri ke kanan. Isi subquery di FROM tidak ikut."""
    name = source_name(node)
    if name is not None:
        return [name]
    result: list[str] = []
    for child in node.children:
        result.extend(source_list(child))
    return result


def output_names(plan: PlanNode, column_names: list[str]) -> list[str]:
    """Nama kolom keluaran SELECT: alias kolom jika ada, nama kolom asli jika tidak.

    `column_names` adalah nama asli kolom keluaran `plan`, urut sesuai Project.
    """
    top = plan
    while isinstance(top, Limit) and top.children:
        top = top.children[0]
    aliases = top.aliases if isinstance(top, Project) else []
    if not aliases:
        return list(column_names)
    return [alias if alias is not None else name for alias, name in zip(aliases, column_names)]
