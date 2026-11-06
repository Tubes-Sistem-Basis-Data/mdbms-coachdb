import unittest

from common.catalog import DataType
from common.expression import And, ColumnRef, Comparison, CompOp, Literal
from common.plan import Filter, Join, JoinKind, Project, Sort, TableScan
from query_optimizer.selection_rewriter import SelectionRewriter

def comparison(table: str, column: str, op: CompOp, value: int) -> Comparison:
    return Comparison(op=op, left=ColumnRef(table, column), right=Literal(value, DataType.INTEGER))

class TestSelectionRewriter(unittest.TestCase):
    def setUp(self) -> None:
        self.rewriter = SelectionRewriter()

    # Memastikan kondisi AND dipecah menjadi beberapa Filter
    def test_split_conjunction(self) -> None:
        first = comparison("student", "nilai", CompOp.GT, 3)
        second = comparison("student", "id", CompOp.EQ, 1)
        third = comparison("student", "id", CompOp.LT, 10)
        original = Filter(condition=And([first, And([second, third])]), children=[TableScan(table="student")])

        result = self.rewriter.apply(original)
        conditions = []
        while isinstance(result, Filter):
            conditions.append(result.condition)
            result = result.children[0]

        self.assertCountEqual(conditions, [first, second, third])
        self.assertTrue(all(not isinstance(condition, And) for condition in conditions))

    # Memastikan kondisi equality diletakkan paling dekat dengan tabel
    def test_reorder_filters(self) -> None:
        equality = comparison("student", "id", CompOp.EQ, 1)
        range_condition = comparison("student", "nilai", CompOp.GT, 3)
        original = Filter(condition=And([equality, range_condition]), children=[TableScan(table="student")])

        result = self.rewriter.apply(original)

        self.assertEqual(result.condition, range_condition)
        self.assertEqual(result.children[0].condition, equality)

    # Memastikan Filter satu tabel masuk ke sisi Join yang sesuai
    def test_pushdown_to_join_sides(self) -> None:
        student_filter = comparison("student", "nilai", CompOp.GT, 3)
        pendaftaran_filter = comparison("pendaftaran", "score", CompOp.GE, 80)
        student = TableScan(table="student")
        pendaftaran = TableScan(table="pendaftaran")
        join = Join(kind=JoinKind.CROSS, children=[student, pendaftaran])
        original = Filter(condition=And([student_filter, pendaftaran_filter]), children=[join])

        result = self.rewriter.apply(original)

        self.assertIsInstance(result, Join)
        self.assertEqual(result.children[0].condition, student_filter)
        self.assertEqual(result.children[1].condition, pendaftaran_filter)

    # Memastikan kondisi dua tabel tetap berada di atas Join
    def test_cross_table_filter_stays_above_join(self) -> None:
        student = TableScan(table="student")
        pendaftaran = TableScan(table="pendaftaran")
        join = Join(kind=JoinKind.CROSS, children=[student, pendaftaran])
        condition = Comparison(op=CompOp.EQ, left=ColumnRef("student", "id"), right=ColumnRef("pendaftaran", "student_id"))
        original = Filter(condition=condition, children=[join])

        result = self.rewriter.apply(original)

        self.assertIsInstance(result, Filter)
        self.assertEqual(result.condition, condition)
        self.assertIsInstance(result.children[0], Join)

    # Memastikan Filter didorong sampai tabel sumber pada Join bertingkat
    def test_pushdown_through_nested_join(self) -> None:
        condition = comparison("student", "nilai", CompOp.GT, 3)
        student = TableScan(table="student")
        pendaftaran = TableScan(table="pendaftaran")
        course = TableScan(table="course")
        inner_join = Join(kind=JoinKind.CROSS, children=[student, pendaftaran])
        outer_join = Join(kind=JoinKind.CROSS, children=[inner_join, course])
        original = Filter(condition=condition, children=[outer_join])

        result = self.rewriter.apply(original)
        rewritten_filter = result.children[0].children[0]

        self.assertIsInstance(rewritten_filter, Filter)
        self.assertEqual(rewritten_filter.condition, condition)
        self.assertIsInstance(rewritten_filter.children[0], TableScan)

    # Memastikan Sort, Project, output columns, dan plan asli tetap aman
    def test_preserves_sort_project_and_input(self) -> None:
        name = ColumnRef("student", "name")
        nilai = ColumnRef("student", "nilai")
        age = ColumnRef("student", "age")
        scan_columns = [name, nilai, age]
        scan = TableScan(table="student", output_columns=scan_columns)
        sort = Sort(column=name, children=[scan], output_columns=scan_columns)
        project = Project(columns=[name, nilai], children=[sort], output_columns=[name, nilai])
        condition = comparison("student", "nilai", CompOp.GT, 3)
        original = Filter(condition=condition, children=[project], output_columns=[name, nilai])

        result = self.rewriter.apply(original)
        rewritten_filter = result.children[0].children[0]

        self.assertIsInstance(result, Project)
        self.assertIsInstance(result.children[0], Sort)
        self.assertIsInstance(rewritten_filter, Filter)
        self.assertEqual(rewritten_filter.condition, condition)
        self.assertEqual(rewritten_filter.output_columns, scan_columns)
        self.assertIsInstance(original, Filter)

if __name__ == "__main__":
    unittest.main()