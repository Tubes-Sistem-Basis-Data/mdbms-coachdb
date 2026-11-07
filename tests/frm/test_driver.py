"""Test the FR-09 driver with the real FRM facade and WAL."""

from __future__ import annotations

import contextlib
import io
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from common.log import RecoveryResult
from drivers.frm_driver import FRMTestDriver, FRMTestScenario, ScenarioFailure, main
from failure_recovery import FailureRecoveryManager


class TestFRMDriver(unittest.TestCase):
    def test_active_scenarios_run_and_pending_recovery_does_not_pass(self) -> None:
        results = FRMTestDriver().run_all()

        self.assertEqual(len(results), 11)
        self.assertEqual(sum(r.passed for r in results), 8)
        self.assertEqual(sum(not r.passed and not r.skipped for r in results), 0)
        self.assertEqual(
            {r.name for r in results if r.skipped},
            {"pitr", "crash-recovery", "multi-tx-recovery"},
        )
        for case in FRMTestDriver().cases[-3:]:
            self.assertTrue(case.actions)
            self.assertIsNotNone(case.expected)

    def test_recovery_cases_activate_when_placeholder_message_is_removed(self) -> None:
        driver = FRMTestDriver()
        completed = RecoveryResult(ok=True, message="recovery complete")
        with patch.object(FailureRecoveryManager, "recover", return_value=completed), \
             patch.object(FailureRecoveryManager, "recover_to", return_value=completed):
            results = [driver.run_case(case) for case in driver.cases[-3:]]

        self.assertTrue(all(not result.skipped for result in results))
        self.assertTrue(all(not result.passed for result in results))
        self.assertTrue(all(result.actual is not None for result in results))

    def test_factory_is_used_for_setup_and_reopen(self) -> None:
        paths = []

        def factory(log_path):
            paths.append(log_path)
            return FailureRecoveryManager(log_path=log_path)

        driver = FRMTestDriver(frm_factory=factory)
        scenario = next(case for case in driver.cases if case.name == "wal-reopen")
        result = driver.run_case(scenario)

        self.assertTrue(result.passed)
        self.assertEqual(len(paths), 2)
        self.assertEqual(paths[0], paths[1])

    def test_scenario_failure_is_not_reported_as_passed(self) -> None:
        class FailingDriver(FRMTestDriver):
            def _wal(self, context) -> None:
                raise ScenarioFailure("WAL result does not match")

        driver = FailingDriver()
        result = driver.run_case(driver.cases[0])

        self.assertFalse(result.passed)
        self.assertFalse(result.skipped)
        self.assertIn("WAL result does not match", result.message)

    def test_run_case_calls_setup_actions_and_cleanup(self) -> None:
        calls = []

        def setup():
            calls.append("setup")
            return SimpleNamespace(value=0, notes=[])

        def first(context):
            calls.append("first")
            context.value = 1

        def second(context):
            calls.append("second")
            return context.value + 1

        def cleanup(context):
            calls.append("cleanup")

        scenario = FRMTestScenario("sample", setup, [first, second], 2, cleanup)
        result = FRMTestDriver().run_case(scenario)

        self.assertTrue(result.passed)
        self.assertEqual(result.actual, 2)
        self.assertEqual(calls, ["setup", "first", "second", "cleanup"])

    def test_cli_runs_one_scenario(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = main(["--scenario", "checkpoint"])

        self.assertEqual(exit_code, 0)
        self.assertIn("[PASSED] checkpoint", output.getvalue())
        self.assertIn("1 passed, 0 failed, 0 not available", output.getvalue())


if __name__ == "__main__":
    unittest.main()
