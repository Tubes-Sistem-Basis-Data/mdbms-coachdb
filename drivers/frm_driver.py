"""FR-09 driver for the Failure Recovery Manager (FRM) test scenarios.
Run ``python3 -m drivers.frm_driver`` for all scenarios, or use ``--list``
and ``--scenario NAME``. Each case uses a temporary WAL.
"""

from __future__ import annotations

import argparse

from drivers.frm_scenarios import (
    FRMCaseContext,
    FRMTestResult,
    FRMTestScenario,
    FeatureNotReady,
    ScenarioFailure,
    _FRMScenarioCatalog,
)


class FRMTestDriver(_FRMScenarioCatalog):
    """Run cases, compare actual results, and report outcomes."""

    def run_case(self, scenario: FRMTestScenario) -> FRMTestResult:
        context = None
        actual = None
        error = None
        not_ready = None
        try:
            context = scenario.setup()
            for action in scenario.actions:
                actual = action(context)
        except FeatureNotReady as exc:
            not_ready = str(exc)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        finally:
            if context is not None:
                try:
                    scenario.cleanup(context)
                except Exception as exc:
                    error = f"cleanup failed: {type(exc).__name__}: {exc}"

        if error is not None:
            return FRMTestResult(scenario.name, False, actual, error)
        if not_ready is not None:
            return FRMTestResult(scenario.name, False, actual, not_ready, skipped=True)
        if actual != scenario.expected:
            return FRMTestResult(
                scenario.name, False, actual,
                f"Result mismatch. Expected {scenario.expected!r}; got {actual!r}",
            )
        return FRMTestResult(scenario.name, True, actual, "; ".join(context.notes))

    def run_all(self) -> list[FRMTestResult]:
        return [self.run_case(scenario) for scenario in self.cases]

    def report_results(self, results: list[FRMTestResult]) -> None:
        case_by_name = {case.name: case for case in self.cases}
        for result in results:
            case = case_by_name.get(result.name)
            title = f"{result.name} ({case.requirement}) - {case.description}" if case else result.name
            status = "NOT AVAILABLE" if result.skipped else "PASSED" if result.passed else "FAILED"
            print(f"[{status}] {title}")
            if result.message:
                print(f"  {result.message}")
        passed = sum(result.passed for result in results)
        skipped = sum(result.skipped for result in results)
        failed = len(results) - passed - skipped
        print(f"Summary: {passed} passed, {failed} failed, {skipped} not available")


def main(argv: list[str] | None = None) -> int:
    driver = FRMTestDriver()
    parser = argparse.ArgumentParser(description="Failure Recovery Manager driver (FR-09)")
    parser.add_argument("--list", action="store_true", help="list available scenarios")
    parser.add_argument("--scenario", choices=[case.name for case in driver.cases],
                        help="run one scenario")
    args = parser.parse_args(argv)
    if args.list:
        for case in driver.cases:
            print(f"{case.name}: {case.requirement} - {case.description}")
        return 0
    cases = [case for case in driver.cases if case.name == args.scenario] if args.scenario else driver.cases
    results = [driver.run_case(case) for case in cases]
    driver.report_results(results)
    return 1 if any(not result.passed and not result.skipped for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
