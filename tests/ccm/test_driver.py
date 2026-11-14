import io
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from textwrap import dedent
from unittest.mock import patch

from common.log import LogKind
from common.storage import RID
from common.transaction import AccessStatus, Action, ProtocolType, ResourceId
from concurrency_control.driver import CCMDriver, ScheduleParser, _MemoryFrm, _SAMPLE_SCHEDULES, main
from concurrency_control.manager import CCManager
from concurrency_control.serializability import Operation, Schedule, SerializabilityResult


def conflict(second: str) -> str:
    return dedent(f"""\
        scenario konflik
        protocol lock
        expect ALLOWED WAIT
        T1 BEGIN
        T2 BEGIN
        T1 W(A)
        T2 {second}(A)
        T1 COMMIT
        T2 COMMIT
        end
        """)


class ScheduleParserTest(unittest.TestCase):
    def test_multiple_scenarios_and_comments(self) -> None:
        text = "# komentar\n" + conflict("R") + conflict("W").replace(
            "T2 W(A)", "T2 W(A) # penulis kedua"
        )
        scenarios = ScheduleParser().parse_text(text)

        self.assertEqual(len(scenarios), 2)
        self.assertEqual(scenarios[0].expected, [AccessStatus.ALLOWED, AccessStatus.WAIT])
        self.assertEqual(scenarios[0].steps[3].kind, "R")
        self.assertEqual(scenarios[1].steps[3].kind, "W")

    def test_invalid_step_reports_line(self) -> None:
        text = conflict("R").replace("T2 R(A)", "T2 R A")
        with self.assertRaisesRegex(ValueError, r"baris 7:"):
            ScheduleParser().parse_text(text)

    def test_status_count_and_uncommitted_transaction(self) -> None:
        for text, line in (
            (conflict("R").replace("expect ALLOWED WAIT", "expect ALLOWED"), 3),
            (conflict("R").replace("T2 COMMIT\n", ""), 9),
        ):
            with self.subTest(line=line), self.assertRaisesRegex(ValueError, f"baris {line}:"):
                ScheduleParser().parse_text(text)


class CCMDriverTest(unittest.TestCase):
    def test_builtin_lock_scenarios(self) -> None:
        scenarios = ScheduleParser().parse_text(_SAMPLE_SCHEDULES)
        for scenario in scenarios:
            with self.subTest(name=scenario.name):
                result = CCMDriver().run(scenario)
                self.assertTrue(result.passed, result.trace)

    def test_victim_aborted_on_retry_excluded_from_serializability(self) -> None:
        class Checker:
            def check(self, schedule: Schedule) -> SerializabilityResult:
                self.tx_ids = {op.tx_id for op in schedule.operations}
                return SerializabilityResult(True, sorted(self.tx_ids))

        scenario, = ScheduleParser().parse_text(dedent("""\
            scenario korban dibatalkan saat retry
            protocol lock
            expect ALLOWED ALLOWED WAIT WAIT
            T1 BEGIN
            T2 BEGIN
            T1 W(A)
            T2 W(B)
            T2 W(A)
            T1 W(B)
            T2 ABORTED
            T1 COMMIT
            end
            """))
        checker = Checker()
        result = CCMDriver(checker=checker).run(scenario)

        self.assertTrue(result.passed, result.trace)
        self.assertIn("T2 W(A) retry -> ABORTED", result.trace)
        self.assertIn("T1 COMMIT -> COMMITTED", result.trace)
        self.assertEqual({op.tx_id for op in result.final_schedule.operations}, {1, 2})
        self.assertEqual(checker.tx_ids, {1})
        self.assertEqual(result.verdict, SerializabilityResult(True, [1]))

    def test_timestamp_header_selects_timestamp_protocol(self) -> None:
        scenario, = ScheduleParser().parse_text(dedent("""\
            scenario timestamp tanpa konflik
            protocol timestamp
            expect ALLOWED
            T1 BEGIN
            T1 R(A)
            T1 COMMIT
            end
            """))
        ccm = CCManager(_MemoryFrm())

        result = CCMDriver(lambda: ccm).run(scenario)

        self.assertTrue(result.passed, result.trace)
        self.assertIs(ccm.get_protocol(), ProtocolType.TIMESTAMP_BASED)

    def test_shared_readers_use_separate_threads(self) -> None:
        class ObservedCCM(CCManager):
            def __init__(self) -> None:
                super().__init__(_MemoryFrm())
                self.threads: dict[int, threading.Thread] = {}

            def begin_transaction(self) -> int:
                tx_id = super().begin_transaction()
                self.threads[tx_id] = threading.current_thread()
                return tx_id

        ccm = ObservedCCM()
        text = conflict("R").replace("T1 W(A)", "T1 R(A)").replace("WAIT", "ALLOWED")
        scenario, = ScheduleParser().parse_text(text)
        result = CCMDriver(lambda: ccm).run(scenario)

        self.assertTrue(result.passed, result.error)
        self.assertEqual(result.statuses, [AccessStatus.ALLOWED, AccessStatus.ALLOWED])
        self.assertEqual(len(set(ccm.threads.values())), 2)
        self.assertNotIn(threading.current_thread(), ccm.threads.values())

    def test_reader_retries_after_writer_commit(self) -> None:
        scenario, = ScheduleParser().parse_text(conflict("R"))
        result = CCMDriver().run(scenario)

        self.assertTrue(result.passed, result.error)
        self.assertEqual(result.statuses, [AccessStatus.ALLOWED, AccessStatus.WAIT])
        self.assertLess(
            result.trace.index("T1 COMMIT -> COMMITTED"),
            result.trace.index("T2 R(A) retry -> ALLOWED"),
        )
        resource = ResourceId("A", RID("A", 0, 0))
        self.assertEqual(
            result.final_schedule.operations,
            [Operation(1, "WRITE", "A", resource.rid), Operation(2, "READ", "A", resource.rid)],
        )
        self.assertIsNone(result.verdict)

    def test_writer_retry_logs_one_data_record(self) -> None:
        frm = _MemoryFrm()
        scenario, = ScheduleParser().parse_text(conflict("W"))
        result = CCMDriver(lambda: CCManager(frm)).run(scenario)

        self.assertTrue(result.passed, result.error)
        self.assertIn("T2 W(A) -> WAIT", result.trace)
        self.assertIn("T2 W(A) retry -> ALLOWED", result.trace)
        data = [record for record in frm.records if record.kind is LogKind.DATA]
        self.assertEqual([record.tx_id for record in data], [1, 2])
        self.assertEqual(data[1].change.rid, RID("A", 0, 0))

    def test_timeout_when_waiting_step_blocks_the_schedule(self) -> None:
        text = conflict("W").replace("T1 COMMIT\nT2 COMMIT", "T2 COMMIT\nT1 COMMIT")
        scenario, = ScheduleParser().parse_text(text)
        with patch("concurrency_control.driver.SCENARIO_TIMEOUT_SECONDS", 0.15):
            result = CCMDriver().run(scenario)

        self.assertFalse(result.passed)
        self.assertIn("melewati batas", result.error)
        self.assertEqual(result.statuses, [AccessStatus.ALLOWED, AccessStatus.WAIT])
        self.assertEqual(len(result.final_schedule.operations), 1)

    def test_run_file_uses_fresh_ccm_per_scenario(self) -> None:
        managers: list[CCManager] = []

        def make_ccm() -> CCManager:
            ccm = CCManager(_MemoryFrm())
            managers.append(ccm)
            return ccm

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "scenarios.txt"
            path.write_text(conflict("W") + conflict("R"), encoding="utf-8")
            results = CCMDriver(make_ccm).run_file(str(path))

        self.assertEqual(len(managers), 2)
        self.assertIsNot(managers[0], managers[1])
        self.assertEqual([result.passed for result in results], [True, True])

    def test_cli_reports_result_and_parse_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "scenarios.txt"
            path.write_text(conflict("R").replace("expect ALLOWED WAIT", "expect ALLOWED ALLOWED"), encoding="utf-8")
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["--file", str(path)]), 1)
            self.assertIn("Serializability: SERIALIZABLE (T1, T2)", output.getvalue())
            self.assertIn("Hasil: GAGAL", output.getvalue())

            path.write_text("rusak", encoding="utf-8")
            with redirect_stderr(io.StringIO()) as errors:
                self.assertEqual(main(["--file", str(path)]), 2)
            self.assertIn("baris 1", errors.getvalue())
