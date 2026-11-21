import unittest

from common.catalog import ColumnSchema, DataType, TableSchema
from common.expression import ColumnRef, Comparison, CompOp, Literal
from common.plan import Filter, Join, JoinKind, ParsedQuery, Project, QueryKind, Sort, SubqueryScan, TableScan
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
        # sisi kiri membawa semua kolomnya, jadi Project identitas dihapus
        self.assertIsInstance(rewritten_join.children[0], TableScan)
        self.assertEqual(rewritten_join.children[1].columns, [pendaftaran_id])

    # Memastikan Project yang kolomnya sama persis dengan keluaran anaknya tidak dipasang
    def test_identity_project_is_dropped(self) -> None:
        kode = ColumnRef("course", "kode")
        sks = ColumnRef("course", "sks")
        condition = Comparison(CompOp.GT, sks, Literal(2, DataType.INTEGER))
        scan = TableScan(table="course", output_columns=[kode, sks])
        original = Project(columns=[kode, sks], children=[Filter(condition=condition, children=[scan])])

        result = self.rewriter.apply(original)

        self.assertIsInstance(result, Project)
        self.assertIsInstance(result.children[0], Filter)
        self.assertIsInstance(result.children[0].children[0], TableScan)

    # Project di bawah SubqueryScan adalah akar plan subquery, tidak boleh ikut dihapus
    def test_project_under_subquery_scan_is_kept(self) -> None:
        kode = ColumnRef("course", "kode")
        scan = TableScan(table="course", output_columns=[kode])
        inner = Project(columns=[kode], children=[scan], output_columns=[kode])
        derived = SubqueryScan(alias="q", children=[inner])
        original = Project(columns=[ColumnRef("q", "kode")], children=[derived])

        result = self.rewriter.apply(original)

        self.assertIsInstance(result.children[0].children[0], Project)

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

    # Memastikan tidak ada Project sisipan di antara Filter berantai dan tidak redundan saat semua kolom dipakai
    def test_chained_filters_no_duplicate_or_redundant_project(self) -> None:
        student_id = ColumnRef("student", "id")
        name = ColumnRef("student", "name")
        gpa = ColumnRef("student", "gpa")
        scan = TableScan(table="student", output_columns=[student_id, name, gpa])
        inner_filter = Filter(condition=Comparison(CompOp.GT, gpa, Literal(3.5, DataType.FLOAT)), children=[scan])
        outer_filter = Filter(condition=Comparison(CompOp.GT, student_id, Literal(10, DataType.INTEGER)), children=[inner_filter])
        original = Project(columns=[name], children=[outer_filter])

        result = self.rewriter.apply(original)

        # Semua kolom (id, name, gpa) dibutuhkan oleh Project + 2 Filter, jadi tidak perlu Project di bawah Filter
        self.assertIsInstance(result, Project)
        self.assertIsInstance(result.children[0], Filter)
        self.assertIsInstance(result.children[0].children[0], Filter)
        self.assertIsInstance(result.children[0].children[0].children[0], TableScan)

    # Memastikan rantai Filter yang memang mengurangi kolom hanya menyisipkan satu Project di bawah Filter terakhir
    def test_chained_filters_single_project_when_pruning(self) -> None:
        student_id = ColumnRef("student", "id")
        name = ColumnRef("student", "name")
        gpa = ColumnRef("student", "gpa")
        scan = TableScan(table="student", output_columns=[student_id, name, gpa])
        inner_filter = Filter(condition=Comparison(CompOp.GT, gpa, Literal(3.0, DataType.FLOAT)), children=[scan])
        outer_filter = Filter(condition=Comparison(CompOp.LT, gpa, Literal(4.0, DataType.FLOAT)), children=[inner_filter])
        original = Project(columns=[name], children=[outer_filter])

        result = self.rewriter.apply(original)

        self.assertIsInstance(result, Project)
        first_filter = result.children[0]
        second_filter = first_filter.children[0]
        pushed_project = second_filter.children[0]
        self.assertIsInstance(first_filter, Filter)
        self.assertIsInstance(second_filter, Filter)
        self.assertIsInstance(pushed_project, Project)
        self.assertEqual(pushed_project.columns, [name, gpa])
        self.assertIsInstance(pushed_project.children[0], TableScan)

    # Memastikan SELECT * dengan Filter pada TableScan tanpa index tidak membuat Project dobel di bawah Filter
    def test_select_all_filter_no_redundant_project(self) -> None:
        student_id = ColumnRef("student", "id")
        name = ColumnRef("student", "name")
        gpa = ColumnRef("student", "gpa")
        scan = TableScan(table="student", output_columns=[student_id, name, gpa])
        filter_node = Filter(condition=Comparison(CompOp.EQ, student_id, Literal(7, DataType.INTEGER)), children=[scan])
        original = Project(columns=[student_id, name, gpa], children=[filter_node])

        result = self.rewriter.apply(original)

        self.assertIsInstance(result, Project)
        self.assertIsInstance(result.children[0], Filter)
        self.assertIsInstance(result.children[0].children[0], TableScan)

    # Memastikan Sort + Filter dengan kolom kebutuhan sama tidak menghasilkan Project dobel mengapit Filter
    def test_sort_above_filter_no_duplicate_project(self) -> None:
        student_id = ColumnRef("student", "id")
        name = ColumnRef("student", "name")
        gpa = ColumnRef("student", "gpa")
        scan = TableScan(table="student", output_columns=[student_id, name, gpa])
        filter_node = Filter(condition=Comparison(CompOp.GT, gpa, Literal(3.5, DataType.FLOAT)), children=[scan])
        sort_node = Sort(column=gpa, ascending=False, children=[filter_node])
        original = Project(columns=[name], aliases=["nama"], children=[sort_node])

        result = self.rewriter.apply(original)

        self.assertIsInstance(result, Project)
        self.assertEqual(result.aliases, ["nama"])
        self.assertIsInstance(result.children[0], Sort)
        self.assertIsInstance(result.children[0].children[0], Filter)
        pushed_project = result.children[0].children[0].children[0]
        self.assertIsInstance(pushed_project, Project)
        self.assertEqual(pushed_project.columns, [name, gpa])
        self.assertIsInstance(pushed_project.children[0], TableScan)

    # Memastikan sisi Join dengan Filter tidak diapit dua Project identik
    def test_join_side_filter_no_duplicate_project(self) -> None:
        student_id = ColumnRef("student", "id")
        name = ColumnRef("student", "name")
        gpa = ColumnRef("student", "gpa")
        dept = ColumnRef("student", "department")
        enroll_sid = ColumnRef("enroll", "student_id")
        grade = ColumnRef("enroll", "grade")

        student_scan = TableScan(table="student", output_columns=[student_id, name, dept, gpa])
        enroll_scan = TableScan(table="enroll", output_columns=[enroll_sid, grade])
        student_filter = Filter(condition=Comparison(CompOp.GT, gpa, Literal(3.5, DataType.FLOAT)), children=[student_scan])
        join = Join(
            kind=JoinKind.THETA,
            condition=Comparison(CompOp.EQ, student_id, enroll_sid),
            children=[student_filter, enroll_scan],
        )
        original = Project(columns=[name, gpa, grade], children=[join])

        result = self.rewriter.apply(original)
        rewritten_join = result.children[0]
        left_side = rewritten_join.children[0]

        self.assertIsInstance(left_side, Filter)
        self.assertIsInstance(left_side.children[0], Project)
        self.assertEqual(left_side.children[0].columns, [name, gpa, student_id])
        self.assertIsInstance(left_side.children[0].children[0], TableScan)

if __name__ == "__main__":
    unittest.main()