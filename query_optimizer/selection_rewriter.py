from __future__ import annotations
from copy import deepcopy
from common.expression import (And, BinaryOp, ColumnRef, Comparison, CompOp, Expression)
from common.plan import Filter, IndexScan, Join, PlanNode, Project, Sort, TableScan

class SelectionRewriter:
    def apply(self, plan: PlanNode) -> PlanNode:
        """Menjalankan seluruh optimasi seleksi"""
        result = deepcopy(plan)
        result = self._split_conjunction(result)
        result = self._push_down(result)
        return self._reorder(result)

    def _split_conjunction(self, plan: PlanNode) -> PlanNode:
        """Memecah kondisi AND menjadi beberapa filter"""

        # Simpan node query plan yang belum diperiksa
        nodes = [plan]
        while nodes:
            node = nodes.pop()

            # Pecah node Filter yang memiliki satu anak dan kondisi AND
            if (isinstance(node, Filter) and len(node.children) == 1 and isinstance(node.condition, And)):
                pending = list(node.condition.conditions)
                conditions: list[Expression] = []

                # Pisah AND jadi kondisi yg terpisah2
                while pending:
                    condition = pending.pop(0)
                    if isinstance(condition, And):
                        pending = list(condition.conditions) + pending
                    else:
                        conditions.append(condition)

                if conditions:
                    # Simpan original child buat dipasang lagi setelah pemecahan
                    original_child = node.children[0]

                    # Pake filter yg sudah ada buat kondisi pertama
                    node.condition = conditions[0]
                    current_filter = node

                    # Susun kondisi berikutnya menjadi Filter yg berantai
                    for condition in conditions[1:]:
                        next_filter = Filter(condition=condition, output_columns=list(node.output_columns))
                        current_filter.children = [next_filter]
                        current_filter = next_filter

                    # Pasang kembali operasi asli di bawah Filter terakhir
                    current_filter.children = [original_child]

            nodes.extend(reversed(node.children))

        return plan

    def _reorder(self, plan: PlanNode) -> PlanNode:
        """Mengatur urutan operasi filter"""

        nodes = [plan]
        while nodes:
            node = nodes.pop()

            if isinstance(node, Filter):
                # Ambil semua Filter yang tersusun berantai
                filter_nodes: list[Filter] = []
                current = node

                while isinstance(current, Filter) and len(current.children) == 1:
                    filter_nodes.append(current)
                    current = current.children[0]

                if len(filter_nodes) >= 2:
                    equality_conditions: list[Expression] = []
                    range_conditions: list[Expression] = []
                    other_conditions: list[Expression] = []

                    # Kelompokkan kondisi berdasarkan jenis perbandingannya
                    for filter_node in filter_nodes:
                        condition = filter_node.condition
                        if isinstance(condition, Comparison) and condition.op is CompOp.EQ:
                            equality_conditions.append(condition)

                        elif isinstance(condition, Comparison) and condition.op in (CompOp.GT, CompOp.GE, CompOp.LT, CompOp.LE):
                            range_conditions.append(condition)

                        else:
                            other_conditions.append(condition)

                    # Letakkan kondisi equality paling dekat dengan sumber data
                    # Urutan filter -> equality -> range -> other
                    ordered_conditions = other_conditions + range_conditions + equality_conditions

                    # Ganti kondisi tiap Filter
                    for index in range(len(filter_nodes)):
                        filter_nodes[index].condition = ordered_conditions[index]

            nodes.extend(reversed(node.children))

        return plan

    def _push_down(self, plan: PlanNode) -> PlanNode:
        """Pindahkan Filter ke subtree tabel yang sesuai"""

        # Proses semua anak sebelum memproses node sekarang
        plan.children = [self._push_down(child) for child in plan.children]

        # Pushdown hanya dilakukan pada filter
        if not isinstance(plan, Filter) or len(plan.children) != 1:
            return plan

        child = plan.children[0]
        condition_columns: list[ColumnRef] = []
        condition_tables: set[str] = set()
        expressions: list[Expression] = [plan.condition]
        unresolved = False

        # Ambil semua kolom dan tabel yang digunakan kondisi flter
        while expressions:
            expression = expressions.pop()
            if isinstance(expression, ColumnRef):
                if expression.table is None:
                    unresolved = True
                else:
                    condition_tables.add(expression.table)

                if expression not in condition_columns:
                    condition_columns.append(expression)

            elif isinstance(expression, And):
                expressions.extend(expression.conditions)

            elif isinstance(expression, (Comparison, BinaryOp)):
                expressions.extend([expression.left, expression.right])

        # Tukar posisi agar Filter sekarang dapat melewati Filter lain
        if isinstance(child, Filter) and len(child.children) == 1:
            plan.children = child.children
            child.children = [self._push_down(plan)]
            return child

        # Tukar posisi agar filter dilakukan sebelum sort
        if isinstance(child, Sort) and len(child.children) == 1:
            plan.children = child.children
            child.children = [self._push_down(plan)]
            return child

        # Lewatin project hanya jika semua kolom kondisi masih tersedia
        if isinstance(child, Project) and len(child.children) == 1:
            columns_available = (not unresolved and all(column in child.columns for column in condition_columns))
            if columns_available:
                project_child = child.children[0]
                plan.children = [project_child]
                plan.output_columns = list(project_child.output_columns)
                child.children = [self._push_down(plan)]
                return child

            return plan

        # Berhenti jika anak bukan Join
        if not isinstance(child, Join) or len(child.children) != 2:
            return plan

        # Berhenti jika binder belum menentukan asal kolom kondisi
        if unresolved or not condition_tables:
            return plan

        left, right = child.children
        side_tables: list[set[str]] = []

        # Ambil semua tabel atau alias pada kedua sisi Join
        for side in (left, right):
            tables: set[str] = set()
            nodes = [side]

            while nodes:
                node = nodes.pop()
                if isinstance(node, (TableScan, IndexScan)):
                    tables.add(getattr(node, "alias", None) or node.table)

                nodes.extend(node.children)

            side_tables.append(tables)

        left_tables, right_tables = side_tables
        only_left = condition_tables <= left_tables and not condition_tables <= right_tables
        only_right = condition_tables <= right_tables and not condition_tables <= left_tables

        # Masukkan Filter ke sisi kiri lalu coba dorong lagi
        if only_left:
            child.children[0] = Filter(condition=plan.condition, children=[left], output_columns=list(left.output_columns))
            return self._push_down(child)

        # Masukkan Filter ke sisi kanan lalu coba dorong lagi
        if only_right:
            child.children[1] = Filter(condition=plan.condition, children=[right], output_columns=list(right.output_columns))
            return self._push_down(child)

        return plan