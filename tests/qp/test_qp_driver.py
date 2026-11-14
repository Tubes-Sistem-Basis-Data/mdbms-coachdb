from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from common.interfaces import QueryProcessor, Session
from common.result import ExecutionResult
from drivers.qp_driver import QPDriver, ScenarioSpec, ScenarioStep, main


class TestQPDriver(unittest.TestCase):
    def setUp(self) -> None:
        self.qp = Mock(spec=QueryProcessor)
        self.opened = []
        def open_session():
            session = Session(len(self.opened) + 1)
            self.opened.append(session)
            return session
        self.qp.open_session.side_effect = open_session
        self.qp.execute_query.return_value = ExecutionResult(ok=True)
        self.driver = QPDriver()

    def test_all_result_fields_and_expected_errors_are_asserted(self) -> None:
        self.qp.execute_query.side_effect = [
            ExecutionResult(True, columns=["name", "id"], rows=[["A;B", 1]], affected_rows=1),
            ExecutionResult(False, message="ERROR: ditolak"),
        ]
        report = self.driver.run_scenarios(self.qp, [ScenarioSpec("hasil", [
            ScenarioStep("SELECT name, id FROM t;", expect_columns=["name", "id"],
                         expect_rows=[["A;B", 1]], expect_affected_rows=1),
            ScenarioStep("SQL salah;", expect_ok=False, expect_error_contains="ditolak"),
        ])])
        self.assertEqual((report.total, report.passed, report.failed), (1, 1, 0))
        self.assertEqual(len(report.outcomes[0].results), 2)
        self.qp.open_session.assert_called_once()
        self.qp.close_session.assert_called_once_with(1)

    def test_sessions_are_reused_by_key_and_closed_between_scenarios(self) -> None:
        report = self.driver.run_scenarios(self.qp, [
            ScenarioSpec("first", [ScenarioStep("a", "a"), ScenarioStep("b", "b"), ScenarioStep("c", "a")]),
            ScenarioSpec("second", [ScenarioStep("d", "a")]),
        ])
        calls = self.qp.execute_query.call_args_list
        self.assertIs(calls[0].args[1], calls[2].args[1])
        self.assertIsNot(calls[0].args[1], calls[1].args[1])
        self.assertIsNot(calls[0].args[1], calls[3].args[1])
        self.assertEqual(self.qp.close_session.call_count, 3)
        self.assertEqual((report.total, report.passed, report.failed), (2, 2, 0))

    def test_wrong_result_stops_scenario_then_continues_the_next(self) -> None:
        self.qp.execute_query.side_effect = lambda sql, session: ExecutionResult(
            True, columns=["wrong"], rows=[[1]], affected_rows=1
        )
        for expectations in (
            {"expect_columns": ["id"]}, {"expect_rows": []},
            {"expect_affected_rows": 0}, {"expect_error_contains": "missing"}, {"expect_ok": False},
        ):
            with self.subTest(expectations=expectations):
                self.qp.reset_mock()
                report = self.driver.run_scenarios(self.qp, [
                    ScenarioSpec("bad", [ScenarioStep("bad", **expectations), ScenarioStep("skipped")]),
                    ScenarioSpec("next", [ScenarioStep("next")]),
                ])
                self.assertEqual((report.total, report.passed, report.failed), (2, 1, 1))
                self.assertIn("Step 1", report.outcomes[0].error_message)
                self.assertEqual([call.args[0] for call in self.qp.execute_query.call_args_list], ["bad", "next"])
                self.assertEqual(self.qp.close_session.call_count, 2)

    def test_unordered_rows_compare_multisets_including_duplicates(self) -> None:
        self.qp.execute_query.return_value = ExecutionResult(True, rows=[[1], [2], [1]])
        for expected, ordered, passed in (([[2], [1], [1]], False, True),
                                           ([[2], [1]], False, False),
                                           ([[2], [1], [1]], True, False)):
            with self.subTest(expected=expected, ordered=ordered):
                report = self.driver.run_scenarios(self.qp, [ScenarioSpec("rows", [
                    ScenarioStep("query", expect_rows=expected, ordered=ordered),
                ])])
                self.assertIs(report.outcomes[0].passed, passed)

    def test_invalid_input_is_rejected_before_opening_any_session(self) -> None:
        cases = (None, [], [None], [ScenarioSpec("", [ScenarioStep("query")])],
                 [ScenarioSpec("empty", [])])
        invalid_steps = (
            {"sql": ""}, {"session_key": ""}, {"expect_ok": 1}, {"ordered": None},
            {"expect_columns": "id"}, {"expect_rows": [[{}]]}, {"expect_rows": [[float("nan")]]},
            {"expect_affected_rows": True}, {"expect_affected_rows": -1}, {"expect_error_contains": 1},
        )
        for scenarios in cases:
            with self.subTest(scenarios=scenarios), self.assertRaises(ValueError):
                self.driver.run_scenarios(self.qp, scenarios)
        for values in invalid_steps:
            step = ScenarioStep("query")
            for key, value in values.items():
                setattr(step, key, value)
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.driver.run_scenarios(self.qp, [ScenarioSpec("valid", [ScenarioStep("query")]),
                                                     ScenarioSpec("invalid", [step])])
        self.qp.open_session.assert_not_called()

    def test_exceptions_and_invalid_return_types_are_failures_not_expected_errors(self) -> None:
        for response in (RuntimeError("execute gagal"), None):
            with self.subTest(response=response):
                self.qp.execute_query.side_effect = response if isinstance(response, Exception) else None
                self.qp.execute_query.return_value = response
                report = self.driver.run_scenarios(self.qp, [ScenarioSpec("failure", [
                    ScenarioStep("query", expect_ok=False),
                ])])
                self.assertEqual(report.failed, 1)
                self.assertIsNotNone(report.outcomes[0].error_message)
        self.assertEqual(self.qp.close_session.call_count, 2)

    def test_cleanup_failure_does_not_skip_other_sessions(self) -> None:
        self.qp.close_session.side_effect = [RuntimeError("close gagal"), None]
        report = self.driver.run_scenarios(self.qp, [ScenarioSpec("cleanup", [
            ScenarioStep("query", "a"), ScenarioStep("query", "b"),
        ])])
        self.assertEqual(report.failed, 1)
        self.assertIn("close gagal", report.outcomes[0].error_message)
        self.assertEqual(self.qp.close_session.call_count, 2)

    def test_pending_transaction_after_cleanup_is_not_reported_as_passed(self) -> None:
        def query(sql, session):
            session.tx_id = 7
            return ExecutionResult(True)
        self.qp.execute_query.side_effect = query
        report = self.driver.run_scenarios(self.qp, [ScenarioSpec("pending", [ScenarioStep("BEGIN;")])])
        self.assertEqual(report.failed, 1)
        self.assertIn("transaksi sesi belum selesai", report.outcomes[0].error_message)

    def test_script_and_report_use_the_declared_json_contract(self) -> None:
        payload = {"scenarios": [{"name": "empty", "steps": [
            {"sql": "SELECT * FROM t;", "expect_columns": [], "expect_rows": [], "ordered": False},
        ]}]}
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "scenarios.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            report = self.driver.run_script(str(path), self.qp)
        output = io.StringIO()
        with redirect_stdout(output):
            self.driver.print_report(report)
        self.assertIn("[LULUS] empty", output.getvalue())
        self.assertIn("Total 1 skenario: 1 lulus, 0 gagal", output.getvalue())

    def test_bad_json_fields_fail_before_query_execution(self) -> None:
        cases = ({}, {"scenarios": []}, {"scenarios": [{"name": "bad", "steps": [
            {"sql": "SELECT 1", "expect_row": []},
        ]}]})
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "scenarios.json"
            for payload in cases:
                path.write_text(json.dumps(payload), encoding="utf-8")
                with self.subTest(payload=payload), self.assertRaises(ValueError):
                    self.driver.run_script(str(path), self.qp)
            path.write_text('{"scenarios": NaN}', encoding="utf-8")
            with self.assertRaises(ValueError):
                self.driver.run_script(str(path), self.qp)
        self.qp.open_session.assert_not_called()


class TestDriverEntryPoint(unittest.TestCase):
    def test_default_runs_regressions_without_bootstrapping_a_database(self) -> None:
        with patch("drivers.qp_driver._run_regressions", return_value=0) as run, \
             patch("main.bootstrap") as bootstrap:
            self.assertEqual(main([]), 0)
        run.assert_called_once()
        bootstrap.assert_not_called()

    def test_empty_or_failed_regression_run_is_not_success(self) -> None:
        from drivers.qp_driver import _run_regressions
        with patch("drivers.qp_driver.unittest.defaultTestLoader.loadTestsFromNames"), \
             patch("drivers.qp_driver.unittest.TextTestRunner") as runner:
            result = runner.return_value.run.return_value
            for total, success, expected in ((0, True, 1), (1, False, 1), (1, True, 0)):
                result.testsRun = total
                result.wasSuccessful.return_value = success
                self.assertEqual(_run_regressions(), expected)

    def test_invalid_script_does_not_bootstrap_or_touch_the_database(self) -> None:
        with patch("main.bootstrap") as bootstrap, redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--script", "/missing/qp-scenarios.json"]), 2)
        bootstrap.assert_not_called()


class TestDriverSQLIntegration(unittest.TestCase):
    def test_json_script_executes_real_sql_operators_and_reports_wrong_data(self) -> None:
        from common.catalog import ColumnSchema, DataType
        from failure_recovery.log import codec
        from storage_manager.facade import StorageManager

        previous_path = codec.get_log_path()
        self.addCleanup(codec.set_log_path, previous_path)
        with TemporaryDirectory() as tmp:
            directory = Path(tmp) / "database"
            storage = StorageManager(str(directory))
            for table, text_column, rows in (
                ("student", "name", [[1, "Ada"], [2, "Budi"]]),
                ("course", "title", [[1, "DB"], [2, "OS"]]),
            ):
                storage._catalog.create_table(table, (
                    ColumnSchema("id", DataType.INTEGER, nullable=False),
                    ColumnSchema(text_column, DataType.VARCHAR, length=50),
                ), ("id",), ())
                storage.bulk_load(table, rows)
            storage._buffer.flush_all()
            storage._disk.close()
            steps = [
                {"sql": "SELECT student.name, course.title FROM student JOIN course ON student.id = course.id ORDER BY student.id DESC;",
                 "expect_columns": ["name", "title"], "expect_rows": [["Budi", "OS"], ["Ada", "DB"]]},
                {"sql": "BEGIN;"},
                {"sql": "INSERT INTO student VALUES (3, 'Cici');", "expect_affected_rows": 1},
                {"sql": "UPDATE student SET name = 'changed' WHERE id = 1;", "expect_affected_rows": 1},
                {"sql": "DELETE FROM student WHERE id = 2;", "expect_affected_rows": 1},
                {"sql": "SELECT id FROM student ORDER BY id;", "expect_rows": [[1], [3]]},
                {"sql": "ABORT;"},
                {"sql": "SELECT * FROM student ORDER BY id;", "expect_rows": [[1, "Ada"], [2, "Budi"]]},
            ]
            payload = {"scenarios": [{"name": "operators and rollback", "steps": steps}]}
            script = Path(tmp) / "scenarios.json"
            instances = []
            def storage_factory(data_dir):
                instance = StorageManager(data_dir)
                instances.append(instance)
                return instance
            try:
                for expected_code, expected_status in ((0, "[LULUS]"), (1, "[GAGAL]")):
                    if expected_code:
                        steps[0]["expect_rows"] = [["wrong", "data"]]
                    script.write_text(json.dumps(payload), encoding="utf-8")
                    output = io.StringIO()
                    with patch("main.StorageManager", side_effect=storage_factory), redirect_stdout(output):
                        code = main(["--script", str(script), "--data-dir", str(directory)])
                    self.assertEqual(code, expected_code, output.getvalue())
                    self.assertIn(expected_status, output.getvalue())
            finally:
                for instance in instances:
                    instance._disk.close()


if __name__ == "__main__":
    unittest.main()
