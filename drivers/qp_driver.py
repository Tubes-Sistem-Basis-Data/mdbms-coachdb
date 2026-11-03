from __future__ import annotations

from dataclasses import dataclass, field

from common.interfaces import QueryProcessor
from common.result import ExecutionResult


@dataclass
class ScenarioStep:
    sql: str
    session_key: str = "default"
    expect_ok: bool = True


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
        return TestReport()

    def run_scenarios(
        self,
        qp: QueryProcessor,
        scenarios: list[ScenarioSpec] | None = None,
    ) -> TestReport:
        return TestReport()


def main() -> None:
    return None


if __name__ == "__main__":
    main()
