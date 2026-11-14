from dataclasses import dataclass, field
from collections import defaultdict, deque
from common.transaction import ResourceId, Action
from common.storage import RID


@dataclass
class Operation:
    tx_id: int
    kind: str
    table: str
    rid: RID | None


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

    def record(
        self,
        tx_id: int,
        resource: ResourceId,
        action: Action,
    ) -> None:
        self.ops.append(
            Operation(
                tx_id=tx_id,
                kind=action.name,
                table=resource.table,
                rid=resource.rid,
            )
        )

    def record_finish(
        self,
        tx_id: int,
        kind: str,
    ) -> None:
        self.ops.append(
            Operation(
                tx_id=tx_id,
                kind=kind,
                table="",
                rid=None,
            )
        )

    def committed_schedule(self) -> Schedule:
        committed = {
            op.tx_id for op in self.ops
            if op.kind == "COMMIT"
        }

        aborted = {
            op.tx_id for op in self.ops
            if op.kind == "ABORT"
        }

        valid = committed - aborted

        return Schedule(
            operations=[
                op for op in self.ops
                if op.tx_id in valid
                and op.kind in ("READ", "WRITE")
            ]
        )


class PrecedenceGraph:
    def __init__(self) -> None:
        self.edges: dict[int, set[int]] = defaultdict(set)

    def _same_resource(
        self,
        first: Operation,
        second: Operation,
    ) -> bool:
        if first.table != second.table:
            return False

        if first.rid is None or second.rid is None:
            return True

        return first.rid == second.rid

    def build(self, schedule: Schedule) -> None:

        self.edges.clear()

        operations = schedule.operations

        for op in operations:
            if op.kind in ("READ", "WRITE"):
                self.edges[op.tx_id]

        for i in range(len(operations)):
            first = operations[i]

            if first.kind not in ("READ", "WRITE"):
                continue

            for j in range(i + 1, len(operations)):
                second = operations[j]

                if second.kind not in ("READ", "WRITE"):
                    continue

                if first.tx_id == second.tx_id:
                    continue

                if not self._same_resource(first, second):
                    continue

                if (
                    first.kind == "READ"
                    and second.kind == "READ"
                ):
                    continue

                self.edges[first.tx_id].add(second.tx_id)

    def find_cycle(self) -> list[int] | None:
        
        visited: set[int] = set()
        recursion_stack: set[int] = set()
        path: list[int] = []

        def dfs(node: int) -> list[int] | None:
            visited.add(node)
            recursion_stack.add(node)
            path.append(node)

            for neighbor in self.edges[node]:

                if neighbor not in visited:
                    cycle = dfs(neighbor)

                    if cycle is not None:
                        return cycle

                elif neighbor in recursion_stack:
                    start = path.index(neighbor)
                    return path[start:] + [neighbor]

            path.pop()
            recursion_stack.remove(node)

            return None

        for node in self.edges:
            if node not in visited:
                cycle = dfs(node)

                if cycle is not None:
                    return cycle

        return None

    def serial_order(self) -> list[int]:

        indegree = {
            node: 0
            for node in self.edges
        }

        for node in self.edges:
            for neighbor in self.edges[node]:
                indegree[neighbor] += 1

        queue = deque(
            node
            for node in indegree
            if indegree[node] == 0
        )

        result: list[int] = []

        while queue:
            node = queue.popleft()
            result.append(node)

            for neighbor in self.edges[node]:
                indegree[neighbor] -= 1

                if indegree[neighbor] == 0:
                    queue.append(neighbor)

        if len(result) != len(indegree):
            return []

        return result


class SerializabilityChecker:
    def check(self, schedule: Schedule) -> SerializabilityResult:

        graph = PrecedenceGraph()

        graph.build(schedule)

        cycle = graph.find_cycle()

        if cycle is not None:
            return SerializabilityResult(
                serializable=False,
                serial_order=[],
                cycle=cycle,
            )

        order = graph.serial_order()

        return SerializabilityResult(
            serializable=True,
            serial_order=order,
            cycle=None,
        )