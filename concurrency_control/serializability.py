from dataclasses import dataclass, field
from collections import defaultdict
from common.transaction import Action, ResourceId


@dataclass
class Operation:
    tx_id: int
    action: Action
    resource: ResourceId


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

    def record(self, tx_id: int, resource: ResourceId, action: Action) -> None:
        raise NotImplementedError

    def committed_schedule(self) -> Schedule:
        raise NotImplementedError


class PrecedenceGraph:
    def __init__(self) -> None:
        self.edges: dict[int, set[int]] = defaultdict(set)

    def build(self, schedule: Schedule) -> None:
        raise NotImplementedError

    def find_cycle(self) -> list[int] | None:
        raise NotImplementedError

    def serial_order(self) -> list[int]:
        raise NotImplementedError


class SerializabilityChecker:
    def check(self, schedule: Schedule) -> SerializabilityResult:
        raise NotImplementedError
