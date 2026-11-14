from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, field, fields
import json
from math import isfinite
from pathlib import Path
import sys
import unittest

from common import config
from common.interfaces import QueryProcessor, Session
from common.result import ExecutionResult


@dataclass
class ScenarioStep:
    sql: str
    session_key: str = "default"
    expect_ok: bool = True
    expect_columns: list[str] | None = None
    expect_rows: list[list] | None = None
    expect_affected_rows: int | None = None
    expect_error_contains: str | None = None
    ordered: bool = True


@dataclass
class ScenarioSpec:
    name: str
    steps: list[ScenarioStep] = field(default_factory=list)


@dataclass
class ScenarioOutcome:
    name: str
    passed: bool
    results: list[ExecutionResult] = field(default_factory=list)
    error_message: str | None = None


@dataclass
class TestReport:
    total: int = 0
    passed: int = 0
    failed: int = 0
    outcomes: list[ScenarioOutcome] = field(default_factory=list)


class QPDriver:
    def run_script(self, path: str, qp: QueryProcessor) -> TestReport:
        return self.run_scenarios(qp, self.load_script(path))

    @staticmethod
    def load_script(path: str) -> list[ScenarioSpec]:
        def reject_constant(value):
            raise ValueError(f"Konstanta JSON tidak valid: {value}")

        with Path(path).open(encoding="utf-8") as source:
            payload = json.load(source, parse_constant=reject_constant)
        if not isinstance(payload, dict) or set(payload) != {"scenarios"}:
            raise ValueError("Root skrip harus berisi field scenarios")
        if not isinstance(payload["scenarios"], list):
            raise ValueError("scenarios harus berupa list")
        scenarios = []
        step_fields = {item.name for item in fields(ScenarioStep)}
        for item in payload["scenarios"]:
            if not isinstance(item, dict) or set(item) != {"name", "steps"}:
                raise ValueError("Setiap skenario harus berisi name dan steps")
            if not isinstance(item["steps"], list):
                raise ValueError("steps harus berupa list")
            steps = []
            for step in item["steps"]:
                if not isinstance(step, dict) or "sql" not in step or set(step) - step_fields:
                    raise ValueError("Field step tidak valid atau sql tidak tersedia")
                steps.append(ScenarioStep(**step))
            scenarios.append(ScenarioSpec(item["name"], steps))
        QPDriver._validate(scenarios)
        return scenarios

    def run_scenarios(
        self,
        qp: QueryProcessor,
        scenarios: list[ScenarioSpec] | None = None,
    ) -> TestReport:
        self._validate(scenarios)
        outcomes = [self._run_scenario(qp, scenario) for scenario in scenarios]
        passed = sum(outcome.passed for outcome in outcomes)
        return TestReport(len(outcomes), passed, len(outcomes) - passed, outcomes)

    @staticmethod
    def _validate(scenarios: list[ScenarioSpec] | None) -> None:
        if not isinstance(scenarios, list) or not scenarios:
            raise ValueError("Daftar skenario tidak boleh kosong")
        for scenario in scenarios:
            if not isinstance(scenario, ScenarioSpec) or not isinstance(scenario.name, str) or not scenario.name.strip():
                raise ValueError("Nama skenario harus berupa string tidak kosong")
            if not isinstance(scenario.steps, list) or not scenario.steps:
                raise ValueError("Skenario harus mempunyai steps")
            for step in scenario.steps:
                if not isinstance(step, ScenarioStep):
                    raise ValueError("Step harus berupa ScenarioStep")
                if not isinstance(step.sql, str) or not step.sql.strip():
                    raise ValueError("SQL tidak boleh kosong")
                if not isinstance(step.session_key, str) or not step.session_key.strip():
                    raise ValueError("session_key tidak boleh kosong")
                if type(step.expect_ok) is not bool or type(step.ordered) is not bool:
                    raise ValueError("expect_ok dan ordered harus boolean")
                if step.expect_columns is not None and (
                    not isinstance(step.expect_columns, list)
                    or any(not isinstance(name, str) for name in step.expect_columns)
                ):
                    raise ValueError("expect_columns harus berupa list string")
                if step.expect_rows is not None:
                    if not isinstance(step.expect_rows, list):
                        raise ValueError("expect_rows harus berupa list baris")
                    for row in step.expect_rows:
                        if not isinstance(row, list) or any(
                            type(value) not in (str, int, float, bool, type(None))
                            or (type(value) is float and not isfinite(value)) for value in row
                        ):
                            raise ValueError("Nilai expect_rows tidak valid")
                if step.expect_affected_rows is not None and (
                    type(step.expect_affected_rows) is not int or step.expect_affected_rows < 0
                ):
                    raise ValueError("expect_affected_rows harus integer nonnegatif")
                if step.expect_error_contains is not None and not isinstance(step.expect_error_contains, str):
                    raise ValueError("expect_error_contains harus berupa string")

    @staticmethod
    def _run_scenario(qp: QueryProcessor, scenario: ScenarioSpec) -> ScenarioOutcome:
        outcome = ScenarioOutcome(scenario.name, passed=True)
        sessions: dict[str, Session] = {}
        index = 0
        try:
            for index, step in enumerate(scenario.steps, 1):
                if step.session_key not in sessions:
                    sessions[step.session_key] = qp.open_session()
                result = qp.execute_query(step.sql, sessions[step.session_key])
                if not isinstance(result, ExecutionResult):
                    raise AssertionError("QP tidak mengembalikan ExecutionResult")
                outcome.results.append(result)
                QPDriver._assert_result(step, result)
        except Exception as error:
            outcome.passed = False
            outcome.error_message = f"Step {index}: {error}"
        finally:
            for key, session in sessions.items():
                try:
                    qp.close_session(session.session_id)
                    if session.tx_id is not None:
                        raise RuntimeError("transaksi sesi belum selesai")
                except Exception as error:
                    outcome.passed = False
                    prefix = f"{outcome.error_message}; " if outcome.error_message else ""
                    outcome.error_message = f"{prefix}cleanup {key}: {error}"
        return outcome

    @staticmethod
    def _assert_result(step: ScenarioStep, result: ExecutionResult) -> None:
        if result.ok is not step.expect_ok:
            raise AssertionError(f"ok diharapkan {step.expect_ok}, diperoleh {result.ok}: {result.message}")
        for label, actual, expected in (
            ("columns", result.columns, step.expect_columns),
            ("affected_rows", result.affected_rows, step.expect_affected_rows),
        ):
            if expected is not None and actual != expected:
                raise AssertionError(f"{label} diharapkan {expected!r}, diperoleh {actual!r}")
        if step.expect_rows is not None:
            actual, expected = result.rows, step.expect_rows
            if not step.ordered:
                actual, expected = Counter(map(tuple, actual)), Counter(map(tuple, expected))
            if actual != expected:
                raise AssertionError(f"rows diharapkan {step.expect_rows!r}, diperoleh {result.rows!r}")
        if step.expect_error_contains is not None and step.expect_error_contains not in result.message:
            raise AssertionError(f"Pesan tidak mengandung {step.expect_error_contains!r}: {result.message}")

    @staticmethod
    def print_report(report: TestReport) -> None:
        for outcome in report.outcomes:
            detail = f": {outcome.error_message}" if outcome.error_message else ""
            print(f"[{'LULUS' if outcome.passed else 'GAGAL'}] {outcome.name}{detail}")
        print(f"Total {report.total} skenario: {report.passed} lulus, {report.failed} gagal")


def _run_regressions() -> int:
    modules = (
        "test_qp01_cli_and_facade", "test_translator", "test_evaluator", "test_scan_project",
        "test_filter", "test_join", "test_sort", "test_insert", "test_update", "test_delete",
        "test_index", "test_context", "test_transactions", "test_context_integration",
        "test_cancellation", "test_disconnect_wait",
    )
    suite = unittest.defaultTestLoader.loadTestsFromNames([f"tests.qp.{name}" for name in modules])
    result = unittest.TextTestRunner(stream=sys.stdout, verbosity=2).run(suite)
    return 0 if result.testsRun and result.wasSuccessful() else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Driver QP: regresi wajib atau skenario SQL JSON")
    parser.add_argument("--script", help="file JSON skenario SQL untuk database yang disiapkan")
    parser.add_argument("--data-dir", default=config.DATA_DIR)
    parser.add_argument("--protocol", choices=("lock", "timestamp"), default=config.DEFAULT_PROTOCOL)
    args = parser.parse_args(argv)
    if args.script is None:
        return _run_regressions()
    try:
        scenarios = QPDriver.load_script(args.script)
        from main import bootstrap, close_runtime, parse_args
        qp = bootstrap(parse_args(["--data-dir", args.data_dir, "--protocol", args.protocol]))
        try:
            report = QPDriver().run_scenarios(qp, scenarios)
        finally:
            close_runtime(qp)
        QPDriver.print_report(report)
        return 1 if report.failed else 0
    except Exception as error:
        print(f"ERROR [DRIVER]: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
