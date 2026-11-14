from __future__ import annotations

import argparse
import re
import sys
import threading
from collections.abc import Callable
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from pathlib import Path
from queue import Empty, Queue, SimpleQueue
from time import monotonic
from typing import TYPE_CHECKING, cast

from common.config import WAIT_RETRY_SECONDS
from common.interfaces import ConcurrencyControlManager, FailureRecoveryManager
from common.log import LogKind, LogRecord, LogResult
from common.storage import RID
from common.transaction import (
    AccessStatus,
    Action,
    ChangeOp,
    DataChange,
    ProtocolType,
    ResourceId,
    TxResult,
    TxStatus,
)
from concurrency_control.manager import CCManager
from concurrency_control.serializability import Operation, Schedule, SerializabilityResult

if TYPE_CHECKING:
    from concurrency_control.serializability import SerializabilityChecker


SCENARIO_TIMEOUT_SECONDS = 5.0
_STEP_PATTERN = re.compile(
    r"T([1-9][0-9]*)\s+(?:(BEGIN|COMMIT|ABORT|ABORTED)|([RW])\(([A-Za-z_][A-Za-z0-9_]*)\))"
)
_PROTOCOLS = {"lock": ProtocolType.LOCK_BASED, "timestamp": ProtocolType.TIMESTAMP_BASED}
_END_STEPS = {"COMMIT", "ABORT", "ABORTED"}

_SAMPLE_SCHEDULES = """
scenario dua pembaca
protocol lock
expect ALLOWED ALLOWED
T1 BEGIN
T2 BEGIN
T1 R(A)
T2 R(A)
T1 COMMIT
T2 COMMIT
end

scenario pembaca menunggu penulis
protocol lock
expect ALLOWED WAIT
T1 BEGIN
T2 BEGIN
T1 W(A)
T2 R(A)
T1 COMMIT
T2 COMMIT
end

scenario dua penulis
protocol lock
expect ALLOWED WAIT
T1 BEGIN
T2 BEGIN
T1 W(A)
T2 W(A)
T1 COMMIT
T2 COMMIT
end

scenario deadlock dua transaksi
protocol lock
expect ALLOWED ALLOWED WAIT ABORTED
T1 BEGIN
T2 BEGIN
T1 W(A)
T2 W(B)
T1 W(B)
T2 W(A)
T2 ABORTED
T1 COMMIT
end

scenario deadlock tiga transaksi
protocol lock
expect ALLOWED ALLOWED ALLOWED WAIT WAIT ABORTED
T1 BEGIN
T2 BEGIN
T3 BEGIN
T1 W(A)
T2 W(B)
T3 W(C)
T1 W(B)
T2 W(C)
T3 W(A)
T3 ABORTED
T2 COMMIT
T1 COMMIT
end

scenario deadlock saat upgrade lock
protocol lock
expect ALLOWED ALLOWED WAIT ABORTED
T1 BEGIN
T2 BEGIN
T1 R(A)
T2 R(A)
T1 W(A)
T2 W(A)
T2 ABORTED
T1 COMMIT
end

scenario pembatalan eksplisit
protocol lock
expect ALLOWED WAIT
T1 BEGIN
T2 BEGIN
T1 W(A)
T2 R(A)
T1 ABORT
T2 COMMIT
end
"""

_TIMESTAMP_SCHEDULES = """
scenario dua pembaca dengan timestamp
protocol timestamp
expect ALLOWED ALLOWED
T1 BEGIN
T2 BEGIN
T1 R(A)
T2 R(A)
T1 COMMIT
T2 COMMIT
end

scenario pembaca lebih tua dari timestamp tulis
protocol timestamp
expect ALLOWED ABORTED
T1 BEGIN
T2 BEGIN
T2 W(A)
T2 COMMIT
T1 R(A)
T1 ABORTED
end

scenario penulis menunggu tulisan yang belum commit
protocol timestamp
expect ALLOWED WAIT
T1 BEGIN
T2 BEGIN
T1 W(A)
T2 W(A)
T1 COMMIT
T2 COMMIT
end
"""


@dataclass(frozen=True)
class _Step:
    tx_id: int
    kind: str
    table: str | None = None

    def __str__(self) -> str:
        command = self.kind if self.table is None else f"{self.kind}({self.table})"
        return f"T{self.tx_id} {command}"


@dataclass
class Scenario:
    # Expected berisi respons pertama setiap R/W, sesuai urutan input.
    name: str
    protocol: ProtocolType
    steps: list[_Step]
    expected: list[AccessStatus]


@dataclass
class ScenarioResult:
    statuses: list[AccessStatus]
    final_schedule: Schedule
    verdict: SerializabilityResult | None
    passed: bool
    trace: list[str]
    error: str | None = None


class ScheduleParser:
    def parse(self, path: str) -> list[Scenario]:
        return self.parse_text(Path(path).read_text(encoding="utf-8"))

    def parse_text(self, text: str) -> list[Scenario]:
        scenarios: list[Scenario] = []
        name: str | None = None
        lines: list[tuple[int, str]] = []
        line_number = 0
        for line_number, raw in enumerate(text.splitlines(), 1):
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            keyword, *rest = line.split(maxsplit=1)
            if name is None:
                if keyword != "scenario" or not rest:
                    raise ValueError(f"baris {line_number}: gunakan 'scenario <nama>'")
                name = rest[0]
            elif line == "end":
                scenarios.append(self._parse_scenario(name, lines, line_number))
                name = None
                lines.clear()
            elif keyword == "scenario":
                raise ValueError(f"baris {line_number}: tutup skenario '{name}' dengan 'end'")
            else:
                lines.append((line_number, line))
        if name is not None:
            raise ValueError(f"baris {line_number + 1}: skenario '{name}' belum ditutup dengan 'end'")
        if not scenarios:
            raise ValueError("baris 1: tidak ada skenario")
        return scenarios

    @staticmethod
    def _parse_scenario(name: str, lines: list[tuple[int, str]], end: int) -> Scenario:
        if not lines:
            raise ValueError(f"baris {end}: metadata 'protocol' belum diisi")
        number, protocol = lines[0]
        parts = protocol.split()
        if len(parts) != 2 or parts[0] != "protocol" or parts[1] not in _PROTOCOLS:
            raise ValueError(f"baris {number}: gunakan 'protocol lock' atau 'protocol timestamp'")
        if len(lines) < 2:
            raise ValueError(f"baris {end}: metadata 'expect' belum diisi")
        expect_line, metadata = lines[1]
        keyword, *statuses = metadata.split()
        if keyword != "expect":
            raise ValueError(f"baris {expect_line}: gunakan 'expect <status...>'")
        try:
            expected = [AccessStatus(status) for status in statuses]
        except ValueError as exc:
            raise ValueError(f"baris {expect_line}: status pada 'expect' tidak valid") from exc

        steps: list[_Step] = []
        active: set[int] = set()
        begun: set[int] = set()
        for number, line in lines[2:]:
            match = _STEP_PATTERN.fullmatch(line)
            if match is None:
                raise ValueError(f"baris {number}: gunakan BEGIN, R(tabel), W(tabel), COMMIT, ABORT, atau ABORTED")
            tx, lifecycle, access, table = match.groups()
            tx_id = int(tx)
            if lifecycle == "BEGIN":
                if tx_id in begun:
                    raise ValueError(f"baris {number}: T{tx_id} sudah BEGIN")
                begun.add(tx_id)
                active.add(tx_id)
            elif tx_id not in active:
                raise ValueError(f"baris {number}: T{tx_id} tidak aktif")
            elif lifecycle in _END_STEPS:
                active.remove(tx_id)
            steps.append(_Step(tx_id, lifecycle or access, table))
        if not begun:
            raise ValueError(f"baris {end}: tidak ada transaksi")
        if active:
            ids = ", ".join(f"T{tx_id}" for tx_id in sorted(active))
            raise ValueError(f"baris {end}: transaksi {ids} belum ditutup dengan COMMIT, ABORT, atau ABORTED")
        if len(expected) != sum(step.table is not None for step in steps):
            raise ValueError(f"baris {expect_line}: jumlah status harus sama dengan jumlah R/W")
        return Scenario(name, _PROTOCOLS[parts[1]], steps, expected)


@dataclass
class _Command:
    step: _Step
    first: Future[None] = field(default_factory=Future)
    done: Future[None] = field(default_factory=Future)


@dataclass(frozen=True)
class _Attempt:
    step: _Step
    status: AccessStatus | TxStatus
    retry: bool = False
    operation: Operation | None = None


class _MemoryFrm:
    # Driver memakai WAL tiruan. Undo data asli diuji lewat FRM dan SM tiruan.
    def __init__(self) -> None:
        self.records: list[LogRecord] = []

    def write_log(self, record: LogRecord) -> LogResult:
        record.lsn = len(self.records) + 1
        self.records.append(record)
        return LogResult(ok=True, lsn=record.lsn)

    def flush_log(self, up_to_lsn: int) -> bool:
        return 0 < up_to_lsn <= len(self.records)

    def rollback(self, tx_id: int) -> TxResult:
        self.write_log(LogRecord(LogKind.ABORT_REQUEST, tx_id=tx_id))
        end = self.write_log(LogRecord(LogKind.ABORT, tx_id=tx_id))
        ok = end.lsn is not None and self.flush_log(end.lsn)
        return TxResult(tx_id, TxStatus.ABORTED if ok else TxStatus.FAILED, ok=ok)


def _make_ccm() -> ConcurrencyControlManager:
    return CCManager(cast(FailureRecoveryManager, _MemoryFrm()))


class CCMDriver:
    def __init__(
        self,
        ccm_factory: Callable[[], ConcurrencyControlManager] = _make_ccm,
        checker: SerializabilityChecker | None = None,
        parser: ScheduleParser | None = None,
    ) -> None:
        self.ccm_factory = ccm_factory
        self.checker = checker
        self.parser = parser if parser is not None else ScheduleParser()

    def run(self, scenario: Scenario) -> ScenarioResult:
        ccm = self.ccm_factory()
        ccm.set_protocol(scenario.protocol)
        stopped = threading.Event()
        order = threading.Lock()
        attempts: SimpleQueue[_Attempt] = SimpleQueue()
        deadline = monotonic() + SCENARIO_TIMEOUT_SECONDS
        queues: dict[int, Queue[_Command | None]] = {}
        threads: list[threading.Thread] = []
        pending: dict[int, _Command] = {}

        def remaining() -> float:
            return max(0.0, deadline - monotonic())

        def worker(inbox: Queue[_Command | None]) -> None:
            tx_id: int | None = None
            aborted = False
            while (command := inbox.get()) is not None and not stopped.is_set():
                step = command.step
                try:
                    if step.kind == "BEGIN":
                        with order:
                            tx_id = ccm.begin_transaction()
                            attempts.put(_Attempt(step, TxStatus.ACTIVE))
                    elif tx_id is None:
                        raise ValueError(f"{step}: transaksi belum BEGIN")
                    elif step.kind == "ABORTED":
                        if not aborted:
                            raise ValueError(f"{step}: transaksi belum dibatalkan oleh CCM")
                        attempts.put(_Attempt(step, TxStatus.ABORTED))
                    elif aborted:
                        raise ValueError(f"{step}: transaksi sudah dibatalkan, gunakan ABORTED")
                    elif step.kind in {"COMMIT", "ABORT"}:
                        commit = step.kind == "COMMIT"
                        with order:
                            result = ccm.commit(tx_id) if commit else ccm.abort(tx_id)
                            attempts.put(_Attempt(step, result.status))
                        expected = TxStatus.COMMITTED if commit else TxStatus.ABORTED
                        if not result.ok or result.status is not expected:
                            raise ValueError(f"{step}: {result.message or 'transaksi gagal diselesaikan'}")
                    else:
                        aborted = self._run_access(ccm, tx_id, command, order, attempts, stopped, remaining)
                    if not command.first.done():
                        command.first.set_result(None)
                    command.done.set_result(None)
                except Exception as exc:
                    if not command.first.done():
                        command.first.set_exception(exc)
                    command.done.set_exception(exc)
                    return

        error = None
        try:
            for step in scenario.steps:
                previous = pending.get(step.tx_id)
                if previous is not None:
                    previous.done.result(timeout=remaining())
                else:
                    inbox: Queue[_Command | None] = Queue()
                    thread = threading.Thread(target=worker, args=(inbox,), daemon=True)
                    thread.start()
                    queues[step.tx_id] = inbox
                    threads.append(thread)

                # Respons pertama WAIT membuka giliran transaksi lain.
                command = _Command(step)
                pending[step.tx_id] = command
                queues[step.tx_id].put(command)
                command.first.result(timeout=remaining())

            for command in pending.values():
                command.done.result(timeout=remaining())
        except (TimeoutError, FutureTimeoutError):
            error = f"skenario melewati batas {SCENARIO_TIMEOUT_SECONDS:g} detik"
        except Exception as exc:
            error = str(exc) or type(exc).__name__
        finally:
            stopped.set()
            for inbox in queues.values():
                inbox.put(None)
            for thread in threads:
                thread.join(timeout=remaining())

        statuses: list[AccessStatus] = []
        schedule = Schedule()
        trace: list[str] = []
        committed: set[int] = set()
        while True:
            try:
                attempt = attempts.get_nowait()
            except Empty:
                break
            retry = " retry" if attempt.retry else ""
            trace.append(f"{attempt.step}{retry} -> {attempt.status.value}")
            if isinstance(attempt.status, AccessStatus) and not attempt.retry:
                statuses.append(attempt.status)
            if attempt.operation is not None:
                schedule.operations.append(attempt.operation)
            if attempt.status is TxStatus.COMMITTED:
                committed.add(attempt.step.tx_id)
        verdict = None
        if self.checker is not None and error is None:
            verdict = self.checker.check(Schedule([op for op in schedule.operations if op.tx_id in committed]))
        passed = bool(scenario.steps) and error is None and statuses == scenario.expected
        if verdict is not None:
            passed = passed and verdict.serializable
        return ScenarioResult(statuses, schedule, verdict, passed, trace, error)

    @staticmethod
    def _run_access(
        ccm: ConcurrencyControlManager,
        tx_id: int,
        command: _Command,
        order: threading.Lock,
        attempts: SimpleQueue[_Attempt],
        stopped: threading.Event,
        remaining: Callable[[], float],
    ) -> bool:
        step = command.step
        if step.table is None or step.kind not in {"R", "W"}:
            raise ValueError(f"{step}: langkah tidak didukung")
        rid = RID(step.table, 0, 0)
        resource = ResourceId(step.table, rid)
        action = Action.READ if step.kind == "R" else Action.WRITE
        change = None
        if action is Action.WRITE:
            change = DataChange(step.table, rid, ChangeOp.UPDATE, [0], [step.tx_id])

        retry = False
        while True:
            # Urutan catatan sama dengan urutan hasil dari facade.
            with order:
                if stopped.is_set() or remaining() <= 0:
                    raise TimeoutError
                response = ccm.validate_object(tx_id, resource, action, change)
                operation = Operation(step.tx_id, action, resource) if response.status is AccessStatus.ALLOWED else None
                attempts.put(_Attempt(step, response.status, retry, operation))
            if response.status is not AccessStatus.WAIT:
                return response.status is AccessStatus.ABORTED
            if not retry:
                command.first.set_result(None)
            retry = True
            stopped.wait(min(WAIT_RETRY_SECONDS, remaining()))

    def run_file(self, path: str) -> list[ScenarioResult]:
        return [self.run(scenario) for scenario in self.parser.parse(path)]


def main(argv: list[str] | None = None, *, checker: SerializabilityChecker | None = None) -> int:
    args_parser = argparse.ArgumentParser(
        description="Jalankan skenario lock atau timestamp CCM",
        epilog="Contoh format file:\n" + _SAMPLE_SCHEDULES.strip().split("\n\n", 1)[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    args_parser.add_argument("--file", help="file skenario. Jika kosong, jalankan contoh bawaan")
    args_parser.add_argument("--protocol", choices=_PROTOCOLS, default="lock", help="protokol contoh bawaan")
    args = args_parser.parse_args(argv)

    driver = CCMDriver(checker=checker)
    try:
        samples = _TIMESTAMP_SCHEDULES if args.protocol == "timestamp" else _SAMPLE_SCHEDULES
        scenarios = driver.parser.parse(args.file) if args.file else driver.parser.parse_text(samples)
    except (OSError, ValueError) as exc:
        print(f"gagal membaca skenario: {exc}", file=sys.stderr)
        return 2

    failed = False
    for scenario in scenarios:
        result = driver.run(scenario)
        print(f"\n{scenario.name}")
        for line in result.trace:
            print(line)
        expected = ", ".join(status.value for status in scenario.expected) or "-"
        actual = ", ".join(status.value for status in result.statuses) or "-"
        schedule = ", ".join(
            str(_Step(op.tx_id, "R" if op.action is Action.READ else "W", op.resource.table))
            for op in result.final_schedule.operations
        )
        print(f"Ekspektasi: {expected}")
        print(f"Respons pertama: {actual}")
        print(f"Jadwal akses: {schedule or '-'}")
        if result.verdict is None:
            print("Serializability: BELUM DIPERIKSA")
        elif result.verdict.serializable:
            serial_order = ", ".join(f"T{tx_id}" for tx_id in result.verdict.serial_order)
            print(f"Serializability: SERIALIZABLE ({serial_order or '-'})")
        else:
            print("Serializability: TIDAK SERIALIZABLE")
        if result.error:
            print(f"Error: {result.error}")
        print(f"Hasil: {'LULUS' if result.passed else 'GAGAL'}")
        failed |= not result.passed
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
