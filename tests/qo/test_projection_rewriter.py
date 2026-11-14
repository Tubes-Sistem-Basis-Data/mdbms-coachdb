import unittest

from common.catalog import ColumnSchema, DataType, TableSchema
from common.expression import ColumnRef, Comparison, CompOp, Literal
from common.plan import Filter, Join, JoinKind, ParsedQuery, Project, QueryKind, Sort, TableScan
from query_optimizer.projection_rewriter import ProjectionRewriter
from query_processor.translator import PlanTranslator
from tests.qp.fakes import FakeCtx, FakeSM

def execute(plan: Project) -> tuple[list[str], list[list[object]]]:
    storage = FakeSM()
    storage.get_schema = lambda table: TableSchema(table, [ColumnSchema("id", DataType.INTEGER), ColumnSchema("name", DataType.VARCHAR, 20)])
    context = FakeCtx(storage)
    query = ParsedQuery(sql="query test", kind=QueryKind.SELECT, plan=plan)
    result = PlanTranslator().build(query, context).execute(context)
    return result.columns, result.rows

class TestProjectionRewriter(unittest.TestCase):
    def setUp(self) -> None:
        self.rewriter = ProjectionRewriter()

    # Memastikan Project berantai disederhanakan menjadi satu Project terluar
    def test_merge_projects(self) -> None:
        name = ColumnRef("student", "name")
        student_id = ColumnRef("student", "id")
        scan = TableScan(table="student")
        inner_project = Project(columns=[name, student_id], children=[scan])
        original = Project(columns=[name], children=[inner_project])

        result = self.rewriter.apply(original)

        self.assertIsInstance(result, Project)
        self.assertEqual(result.columns, [name])
        self.assertIsInstance(result.children[0], TableScan)

    # Memastikan setiap sisi Join hanya membawa kolom hasil, Filter, dan Sort yang dibutuhkan
    def test_pushdown_required_columns(self) -> None:
        name = ColumnRef("student", "name")
        age = ColumnRef("student", "age")
        nilai = ColumnRef("student", "nilai")
        pendaftaran_id = ColumnRef("pendaftaran", "id")
        score = ColumnRef("pendaftaran", "score")
        condition = Comparison(CompOp.GE, nilai, Literal(80, DataType.INTEGER))
        student = TableScan(table="student", output_columns=[name, age, nilai])
        pendaftaran = TableScan(table="pendaftaran", output_columns=[pendaftaran_id, score])
        join = Join(kind=JoinKind.CROSS, children=[student, pendaftaran])
        filter_node = Filter(condition=condition, children=[join])
        sort = Sort(column=age, children=[filter_node])
        original = Project(columns=[name, name], children=[sort])

        result = self.rewriter.apply(original)
        sort_project = result.children[0].children[0]
        filter_project = sort_project.children[0].children[0]
        rewritten_join = filter_project.children[0]

        self.assertEqual(result.columns, [name, name])
        self.assertEqual(result.children[0].output_columns, [name, age])
        self.assertEqual(sort_project.children[0].output_columns, [name, age, nilai])
        self.assertEqual(rewritten_join.output_columns, [name, age, nilai, pendaftaran_id])
        self.assertEqual(rewritten_join.children[0].columns, [name, age, nilai])
        self.assertEqual(rewritten_join.children[1].columns, [pendaftaran_id])

    # Memastikan kolom kondisi Join tetap dibawa walaupun tidak ditampilkan
    def test_preserve_join_columns(self) -> None:
        name = ColumnRef("student", "name")
        student_id = ColumnRef("student", "id")
        pendaftaran_id = ColumnRef("pendaftaran", "student_id")
        condition = Comparison(CompOp.EQ, student_id, pendaftaran_id)
        join = Join(kind=JoinKind.THETA, condition=condition, children=[TableScan(table="student"), TableScan(table="pendaftaran")])
        original = Project(columns=[name], children=[join])

        result = self.rewriter.apply(original)
        rewritten_join = result.children[0]

        self.assertEqual(rewritten_join.children[0].columns, [name, student_id])
        self.assertEqual(rewritten_join.children[1].columns, [pendaftaran_id])

    # Memastikan urutan kolom hasil tetap mengikuti urutan SELECT
    def test_preserve_select_order(self) -> None:
        name = ColumnRef("student", "name")
        student_id = ColumnRef("student", "id")
        original = Project(columns=[name, student_id, name], children=[TableScan(table="student")])

        result = self.rewriter.apply(original)

        self.assertEqual(result.columns, [name, student_id, name])

    # Memastikan hasil eksekusi sebelum dan sesudah optimasi tetap sama
    def test_preserve_execution_result(self) -> None:
        name = ColumnRef("t", "name")
        student_id = ColumnRef("t", "id")
        condition = Comparison(CompOp.GE, student_id, Literal(1, DataType.INTEGER))
        filter_node = Filter(condition=condition, children=[TableScan(table="t")])
        sort = Sort(column=student_id, ascending=False, children=[filter_node])
        original = Project(columns=[name, name], children=[sort])

        original_result = execute(original)
        optimized_result = execute(self.rewriter.apply(original))

        self.assertEqual(optimized_result, original_result)

if __name__ == "__main__":
    unittest.main()