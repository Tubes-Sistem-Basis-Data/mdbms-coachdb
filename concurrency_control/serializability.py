from dataclasses import dataclass, field
from collections import defaultdict


@dataclass
class Operation:
    tx_id: int
    kind: str
    item: str


@dataclass
class Schedule:
    operations: list[Operation] = field(default_factory=list)


@dataclass
class SerializabilityResult:
    serializable: bool
    serial_order: list[int]
    cycle: list[int] | None = None


class ScheduleRecorder:
    def __init__(self) -> None:
        self.ops: list[Operation] = []

    def record(self, tx_id: int, kind: str, item: str,) -> None:
        ...

    def committed_schedule(self) -> Schedule:
        ...


class PrecedenceGraph:
    def __init__(self) -> None:
        self.edges: dict[int, set[int]] = defaultdict(set)

    def build(self, schedule: Schedule) -> None:
        ...

    def find_cycle(self) -> list[int] | None:
        ...

    def serial_order(self) -> list[int]:
        ...


class SerializabilityChecker:
    def check(self, schedule: Schedule) -> SerializabilityResult:
        ...