from copy import deepcopy

from common.expression import And, BinaryOp, ColumnRef, Comparison, Expression
from common.plan import Filter, IndexScan, Join, JoinKind, PlanNode, Project, Sort, TableScan

class ProjectionRewriter:
    def apply(self, plan: PlanNode) -> PlanNode:
        """Menjalankan seluruh optimasi proyeksi"""
        result = deepcopy(plan)
        result = self._merge_projects(result)
        result = self._push_down(result)
        return self._merge_projects(result)

    def _merge_projects(self, plan: PlanNode) -> PlanNode:
        """Menggabungkan Project yang  berurutan"""
        nodes = [plan]

        while nodes:
            node = nodes.pop()

            # Pertahankan Project terluar dan hapus Project langsung di bawahnya
            if isinstance(node, Project):
                while len(node.children) == 1 and isinstance(node.children[0], Project):
                    node.children = node.children[0].children

            nodes.extend(reversed(node.children))

        return plan

    def _push_down(self, plan: PlanNode) -> PlanNode:
        """Meneruskan hanya kolom yang masih dibutuhkan"""

        # Cari Project di seluruh bagian plan
        if not isinstance(plan, Project) or len(plan.children) != 1:
            rewritten_children: list[PlanNode] = []

            for child_node in plan.children:
                rewritten_child = self._push_down(child_node)
                rewritten_children.append(rewritten_child)

            plan.children = rewritten_children
            return plan

        child = plan.children[0]
        required_columns: list[ColumnRef] = []

        # Ambil kolom Project tanpa membawa duplikat ke Project internal
        for column in plan.columns:
            if column not in required_columns:
                required_columns.append(column)

        # Ambil kondisi Filter atau Join yang berada tepat di bawah Project
        pending_expressions: list[Expression] = []

        if isinstance(child, Filter):
            pending_expressions.append(child.condition)
            
        elif isinstance(child, Join) and child.condition is not None:
            pending_expressions.append(child.condition)

        # Cari semua kolom yang digunakan oleh kondisi Filter atau Join
        while pending_expressions:
            expression = pending_expressions.pop()

            if isinstance(expression, ColumnRef):
                if expression.table is None:
                    plan.children[0] = self._push_down(child)
                    return plan

                if expression not in required_columns:
                    required_columns.append(expression)

                continue

            if isinstance(expression, And):
                for condition in expression.conditions:
                    pending_expressions.append(condition)

                continue

            if isinstance(expression, (Comparison, BinaryOp)):
                pending_expressions.append(expression.left)
                pending_expressions.append(expression.right)

        # Letakkan Project di bawah Filter agar kolom kondisi tetap tersedia
        if isinstance(child, Filter) and len(child.children) == 1:
            filter_child = child.children[0]
            pushed_project = Project(columns=required_columns, children=[filter_child], output_columns=list(required_columns))

            child.children[0] = self._push_down(pushed_project)
            child.output_columns = list(required_columns)
            return plan

        # Letakkan Project di bawah Sort dan pertahankan kolom pengurutan
        if isinstance(child, Sort) and len(child.children) == 1:
            if child.column.table is None:
                plan.children[0] = self._push_down(child)
                return plan

            if child.column not in required_columns:
                required_columns.append(child.column)

            sort_child = child.children[0]
            pushed_project = Project(columns=required_columns, children=[sort_child], output_columns=list(required_columns))

            child.children[0] = self._push_down(pushed_project)
            child.output_columns = list(required_columns)
            return plan

        # Letakkan Project pada sisi kiri dan kanan Join
        if isinstance(child, Join) and len(child.children) == 2:
            left, right = child.children

            # Natural Join membutuhkan kolom dengan nama sama pada kedua sisi
            if child.kind is JoinKind.NATURAL:
                left_names: set[str] = set()
                right_names: set[str] = set()

                for column in left.output_columns:
                    left_names.add(column.column)

                for column in right.output_columns:
                    right_names.add(column.column)

                shared_names = left_names & right_names
                join_columns = left.output_columns + right.output_columns

                for column in join_columns:
                    is_shared_column = column.column in shared_names
                    is_new_column = column not in required_columns

                    if is_shared_column and is_new_column:
                        required_columns.append(column)

            # Cari nama tabel atau alias pada setiap sisi Join
            side_tables: list[set[str]] = []
            join_sides = [left, right]

            for join_side in join_sides:
                tables: set[str] = set()
                pending_nodes = [join_side]

                while pending_nodes:
                    current_node = pending_nodes.pop()

                    if isinstance(current_node, TableScan):
                        table_name = current_node.alias

                        if table_name is None:
                            table_name = current_node.table

                        tables.add(table_name)

                    elif isinstance(current_node, IndexScan):
                        tables.add(current_node.table)

                    for nested_child in current_node.children:
                        pending_nodes.append(nested_child)

                side_tables.append(tables)

            left_tables = side_tables[0]
            right_tables = side_tables[1]
            left_required: list[ColumnRef] = []
            right_required: list[ColumnRef] = []

            # Pisahkan setiap kolom berdasarkan tabel asalnya
            for column in required_columns:
                belongs_to_left = column.table in left_tables
                belongs_to_right = column.table in right_tables
                belongs_to_both = belongs_to_left and belongs_to_right
                belongs_to_neither = not belongs_to_left and not belongs_to_right

                if belongs_to_both or belongs_to_neither:
                    plan.children[0] = self._push_down(child)
                    return plan

                if belongs_to_left:
                    left_required.append(column)
                else:
                    right_required.append(column)

            # Project tidak boleh kosong, jadi sisi kosong membawa satu kolom
            if not left_required and left.output_columns:
                left_required.append(left.output_columns[0])

            if not right_required and right.output_columns:
                right_required.append(right.output_columns[0])

            # Buat Project untuk sisi kiri Join
            if left_required:
                left_project = Project(columns=left_required, children=[left], output_columns=list(left_required))

                child.children[0] = self._push_down(left_project)

            # Buat Project untuk sisi kanan Join
            if right_required:
                right_project = Project(columns=right_required, children=[right], output_columns=list(right_required))

                child.children[1] = self._push_down(right_project)

            # Perbarui daftar kolom keluaran Join
            if child.kind is JoinKind.NATURAL:
                natural_output: list[ColumnRef] = []

                for column in child.output_columns:
                    if column in required_columns:
                        natural_output.append(column)

                child.output_columns = natural_output

            else:
                join_output = list(left_required)
                join_output.extend(right_required)
                child.output_columns = join_output

            return plan

        # Jika Project tidak bisa turun, tetap periksa bagian di bawahnya
        plan.children[0] = self._push_down(child)
        return plan