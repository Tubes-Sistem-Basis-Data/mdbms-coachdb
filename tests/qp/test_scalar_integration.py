from __future__ import annotations

import copy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from common.catalog import ColumnSchema, DataType
from common.expression import And, ColumnRef, Comparison, CompOp, Literal, Subquery
from common.interfaces import QueryOptimizer
from common.log import LogKind
from common.plan import (
    Delete, Filter, Join, JoinKind, ParsedQuery, Project, QueryKind, TableScan, TransactionCommand, Update,
)
from concurrency_control import CCManager
from failure_recovery import FailureRecoveryManager
from failure_recovery.log import codec
from query_processor.facade import QueryProcessorFacade
from storage_manager.facade import StorageManager


def ref(table: str, column: str = "id") -> ColumnRef:
    return ColumnRef(table, column)


def project(source, *columns) -> Project:
    return Project(columns=list(columns), children=[source])


class TestScalarIntegration(unittest.TestCase):
    def setUp(self) -> None:
        previous_path = codec.get_log_path()
        self.addCleanup(codec.set_log_path, previous_path)
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.sm = StorageManager(data_dir=temp.name)
        self.addCleanup(self.sm._disk.close)
        # Setup katalog selama facade DDL SM belum diimplementasikan.
        self.sm._catalog.create_table("student", (
            ColumnSchema("id", DataType.INTEGER, nullable=False),
            ColumnSchema("gpa", DataType.FLOAT), ColumnSchema("name", DataType.VARCHAR, length=50),
        ), ("id",), ())
        self.sm._catalog.create_table("threshold", (ColumnSchema("gpa", DataType.FLOAT),), (), ())
        self.original = [[1, 3.0, "Ada"], [2, 4.0, "Budi"], [3, 2.5, "Cici"]]
        self.sm.bulk_load("student", self.original)
        self.sm.bulk_load("threshold", [[3.2]])
        self.frm = FailureRecoveryManager(log_path=str(Path(temp.name) / "wal.log"))
        self.addCleanup(self.frm.close)
        self.ccm = CCManager(self.frm)
        self.frm.set_dependencies(self.sm, self.ccm)
        self.sm.set_log_flusher(self.frm.flush_log)
        self.qo = Mock(spec=QueryOptimizer)
        self.qo.parse_query.side_effect = self.parse_manual_plan
        self.qo.optimize_query.side_effect = lambda parsed: parsed
        self.qp = QueryProcessorFacade(self.qo, self.sm, self.ccm, self.frm)
        self.session = self.qp.open_session()
        self.addCleanup(self.qp.close_session, self.session.session_id)

    def parse_manual_plan(self, sql: str) -> ParsedQuery:
        command = sql.strip().rstrip(";").upper()
        if command in ("BEGIN", "COMMIT", "ABORT"):
            return ParsedQuery(sql, QueryKind.TRANSACTION, command=TransactionCommand(command))
        return ParsedQuery(sql, self.kind, plan=self.plan)

    def execute(self, plan, kind=QueryKind.SELECT):
        self.plan, self.kind = plan, kind
        return self.qp.execute_query("manual plan;", self.session)

    def condition(self, operand=None):
        if operand is None:
            operand = Subquery(project(TableScan(table="threshold"), ref("threshold", "gpa")))
        return Comparison(CompOp.GT, ref("student", "gpa"), operand)

    def source(self, condition=None):
        return Filter(condition=condition if condition is not None else self.condition(),
                      children=[TableScan(table="student")])

    def test_select_reads_scalar_once_in_the_outer_transaction(self) -> None:
        plan = project(self.source(), ref("student"))
        original = copy.deepcopy(plan)
        with patch.object(self.ccm, "begin_transaction", wraps=self.ccm.begin_transaction) as begin, \
             patch.object(self.ccm, "validate_object", wraps=self.ccm.validate_object) as authorize, \
             patch.object(self.sm, "scan", wraps=self.sm.scan) as scan:
            result = self.execute(plan)

        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.rows, [[2]])
        self.assertEqual(plan, original)
        begin.assert_called_once()
        self.assertEqual(sum(call.args == ("threshold",) for call in scan.call_args_list), 1)
        tx_id = self.frm.read_log()[0].tx_id
        self.assertTrue(all(call.args[0] == tx_id for call in authorize.call_args_list))
        self.assertEqual([record.kind for record in self.frm.read_log()], [LogKind.BEGIN, LogKind.COMMIT])

    def test_scalar_cache_is_fresh_in_each_statement_of_an_explicit_transaction(self) -> None:
        self.assertTrue(self.qp.execute_query("BEGIN;", self.session).ok)
        tx_id = self.session.tx_id
        plan = project(self.source(), ref("student"))
        with patch.object(self.sm, "scan", wraps=self.sm.scan) as scan:
            self.assertEqual(self.execute(plan).rows, [[2]])
            update = Update(table="threshold", column="gpa", expression=Literal(2.0, DataType.FLOAT),
                            children=[TableScan(table="threshold")])
            self.assertTrue(self.execute(update, QueryKind.UPDATE).ok)
            self.assertEqual(self.execute(plan).rows, [[1], [2], [3]])
        self.assertEqual(self.session.tx_id, tx_id)
        self.assertEqual(sum(call.args == ("threshold",) for call in scan.call_args_list), 3)
        self.assertTrue(self.qp.execute_query("ABORT;", self.session).ok)
        self.assertEqual([row.values for row in self.sm.scan("threshold")], [[3.2]])

    def test_scalar_cache_is_not_shared_with_another_session(self) -> None:
        plan = project(self.source(), ref("student"))
        self.assertEqual(self.execute(plan).rows, [[2]])
        other = self.qp.open_session()
        self.addCleanup(self.qp.close_session, other.session_id)
        self.plan, self.kind = plan, QueryKind.SELECT
        with patch.object(self.sm, "scan", wraps=self.sm.scan) as scan:
            result = self.qp.execute_query("manual plan;", other)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.rows, [[2]])
        self.assertEqual(sum(call.args == ("threshold",) for call in scan.call_args_list), 1)

    def assert_dml_with_scalar_can_abort(self, kind: QueryKind) -> None:
        self.assertTrue(self.qp.execute_query("BEGIN;", self.session).ok)
        if kind is QueryKind.UPDATE:
            plan = Update(table="student", column="name", expression=Literal("selected", DataType.VARCHAR),
                          children=[self.source()])
            expected = [[1, 3.0, "Ada"], [2, 4.0, "selected"], [3, 2.5, "Cici"]]
        else:
            plan = Delete(table="student", children=[self.source()])
            expected = [self.original[0], self.original[2]]
        tx_id = self.session.tx_id

        result = self.execute(plan, kind)

        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.affected_rows, 1)
        self.assertEqual([row.values for row in self.sm.scan("student")], expected)
        self.assertTrue(self.qp.execute_query("ABORT;", self.session).ok)
        self.assertEqual([row.values for row in self.sm.scan("student")], self.original)
        self.assertEqual([record.kind for record in self.frm.read_log(tx_id)],
                         [LogKind.BEGIN, LogKind.DATA, LogKind.ABORT_REQUEST, LogKind.ABORT])

    def test_update_condition_uses_scalar_and_abort_restores_data(self) -> None:
        self.assert_dml_with_scalar_can_abort(QueryKind.UPDATE)

    def test_delete_condition_uses_scalar_and_abort_restores_data(self) -> None:
        self.assert_dml_with_scalar_can_abort(QueryKind.DELETE)

    def test_empty_scalar_is_null_and_filters_out_all_rows(self) -> None:
        inner = project(Filter(condition=Comparison(CompOp.LT, ref("threshold", "gpa"),
                                                     Literal(0.0, DataType.FLOAT)),
                               children=[TableScan(table="threshold")]), ref("threshold", "gpa"))
        result = self.execute(project(self.source(self.condition(Subquery(inner))), ref("student")))
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.columns, ["id"])
        self.assertEqual(result.rows, [])

    def test_scalar_cardinality_error_aborts_without_writing(self) -> None:
        inner = Subquery(project(TableScan(table="student"), ref("student")))
        result = self.execute(project(self.source(self.condition(inner)), ref("student")))

        self.assertFalse(result.ok)
        self.assertIn("lebih dari satu baris", result.message)
        self.assertIsNone(self.session.tx_id)
        self.assertEqual([row.values for row in self.sm.scan("student")], self.original)
        self.assertEqual([record.kind for record in self.frm.read_log()],
                         [LogKind.BEGIN, LogKind.ABORT_REQUEST, LogKind.ABORT])

    def test_short_circuit_does_not_run_a_scalar_that_would_fail_cardinality(self) -> None:
        scalar = Subquery(project(TableScan(table="student"), ref("student")))
        condition = And([
            Comparison(CompOp.EQ, ref("student"), Literal(-1, DataType.INTEGER)), self.condition(scalar),
        ])
        with patch.object(self.sm, "scan", wraps=self.sm.scan) as scan:
            result = self.execute(project(self.source(condition), ref("student")))
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.rows, [])
        scan.assert_called_once_with("student")

    def test_nested_scalar_uses_the_same_statement_cache(self) -> None:
        inner = Subquery(project(TableScan(table="threshold"), ref("threshold", "gpa")))
        condition = And([Comparison(CompOp.EQ, ref("student"), Literal(2, DataType.INTEGER)),
                         self.condition(inner)])
        middle = Subquery(project(self.source(condition), ref("student", "gpa")))
        outer = Comparison(CompOp.LE, ref("student", "gpa"), middle)
        with patch.object(self.sm, "scan", wraps=self.sm.scan) as scan:
            result = self.execute(project(self.source(outer), ref("student")))
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.rows, [[1], [2], [3]])
        self.assertEqual(sum(call.args == ("threshold",) for call in scan.call_args_list), 1)
        self.assertEqual(sum(call.args == ("student",) for call in scan.call_args_list), 2)

    def test_self_join_condition_uses_scalar_resolver(self) -> None:
        scalar = Subquery(project(TableScan(table="threshold"), ref("threshold", "gpa")))
        join = Join(kind=JoinKind.THETA, condition=And([
            Comparison(CompOp.EQ, ref("a"), ref("b")), Comparison(CompOp.GT, ref("a", "gpa"), scalar),
        ]), children=[TableScan(table="student", alias="a"), TableScan(table="student", alias="b")])
        result = self.execute(project(join, ref("a"), ref("b", "name")))
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.rows, [[2, "Budi"]])


if __name__ == "__main__":
    unittest.main()
