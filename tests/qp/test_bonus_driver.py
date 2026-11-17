from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from common.interfaces import ConcurrencyControlManager
from common.transaction import AccessResponse, AccessStatus, TxResult, TxStatus
from drivers.qp_driver import QPDriver, ScenarioSpec, ScenarioStep
from query_optimizer import QueryOptimizerFacade
from query_processor.ddl import DDLCoordinator
from query_processor.facade import QueryProcessorFacade
from tests.qp.test_bonus_facade import CatalogMutatingFakeSM, DDLTestTranslator


class TestBonusDriver(unittest.TestCase):
    def setUp(self) -> None:
        self.sm = CatalogMutatingFakeSM()
        self.ccm = Mock(spec=ConcurrencyControlManager)
        self.ccm.begin_transaction.side_effect = list(range(1, 100))
        self.ccm.commit.side_effect = lambda tx: TxResult(tx, TxStatus.COMMITTED, True)
        self.ccm.abort.side_effect = lambda tx: TxResult(tx, TxStatus.ABORTED, True)
        self.ccm.validate_object.return_value = AccessResponse(AccessStatus.ALLOWED, lsn=1)
        self.qo = QueryOptimizerFacade(self.sm)
        self.translator = DDLTestTranslator()
        self.qp = QueryProcessorFacade(
            qo=self.qo,
            sm=self.sm,
            ccm=self.ccm,
            translator=self.translator,
        )
        self.qp._ddl = DDLCoordinator(
            transactions=self.qp._transactions,
            allow_ddl=True,
            formatter=self.qp._formatter,
            admin_lock=self.qp._ddl.admin_lock,
        )
        self.driver = QPDriver()

    def test_combined_bonus_scenarios_pass_through_driver_and_json_script(self) -> None:
        payload = {
            "scenarios": [
                {
                    "name": "limit, alias, dan self-join",
                    "steps": [
                        {
                            "sql": "SELECT s.id AS nim, s.name AS nama FROM student AS s ORDER BY s.id LIMIT 2;",
                            "session_key": "client-a",
                            "expect_ok": True,
                            "expect_columns": ["nim", "nama"],
                            "expect_rows": [[1, "Ada"], [2, "Budi"]],
                            "ordered": True,
                        },
                        {
                            "sql": "SELECT a.name AS kiri, b.name AS kanan FROM student a JOIN student b ON a.id = b.id;",
                            "session_key": "client-a",
                            "expect_ok": True,
                            "expect_columns": ["kiri", "kanan"],
                            "expect_rows": [["Cici", "Cici"], ["Ada", "Ada"], ["Budi", "Budi"]],
                            "ordered": False,
                        },
                    ],
                },
                {
                    "name": "derived table dan scalar subquery",
                    "steps": [
                        {
                            "sql": "SELECT q.nim FROM (SELECT id AS nim FROM student ORDER BY id LIMIT 2) AS q ORDER BY q.nim;",
                            "expect_ok": True,
                            "expect_columns": ["nim"],
                            "expect_rows": [[1], [2]],
                            "ordered": True,
                        },
                        {
                            "sql": "SELECT id FROM student WHERE gpa > (SELECT gpa FROM student WHERE id = 1) ORDER BY id;",
                            "expect_ok": True,
                            "expect_columns": ["id"],
                            "expect_rows": [[2]],
                            "ordered": True,
                        },
                    ],
                },
                {
                    "name": "ddl mutasi katalog dengan restrict dan cascade",
                    "steps": [
                        {
                            "sql": "CREATE TABLE note (id INTEGER PRIMARY KEY, student_id INTEGER REFERENCES student(id));",
                            "expect_ok": True,
                            "expect_affected_rows": 0,
                        },
                        {
                            "sql": "INSERT INTO note VALUES (10, 1);",
                            "expect_ok": True,
                            "expect_affected_rows": 1,
                        },
                        {
                            "sql": "SELECT n.id AS note_id FROM note AS n LIMIT 1;",
                            "expect_ok": True,
                            "expect_columns": ["note_id"],
                            "expect_rows": [[10]],
                        },
                        {
                            "sql": "DROP TABLE student RESTRICT;",
                            "expect_ok": False,
                            "expect_error_contains": "student",
                        },
                        {
                            "sql": "DROP TABLE student CASCADE;",
                            "expect_ok": True,
                            "expect_affected_rows": 0,
                        },
                        {
                            "sql": "SELECT * FROM note;",
                            "expect_ok": False,
                            "expect_error_contains": "note",
                        },
                    ],
                },
            ]
        }

        with TemporaryDirectory() as tmp:
            script_path = Path(tmp) / "bonus_scenarios.json"
            script_path.write_text(json.dumps(payload), encoding="utf-8")
            report = self.driver.run_script(str(script_path), self.qp)

        self.assertEqual((report.total, report.passed, report.failed), (3, 3, 0))
        self.assertTrue(all(outcome.passed for outcome in report.outcomes))

    def test_driver_detects_wrong_alias_header_row_count_and_values_when_result_is_ok(self) -> None:
        scenarios = [
            ScenarioSpec(
                "header salah",
                [
                    ScenarioStep(
                        "SELECT s.id AS nim FROM student AS s ORDER BY s.id LIMIT 2;",
                        expect_ok=True,
                        expect_columns=["id"],
                        expect_rows=[[1], [2]],
                    )
                ],
            ),
            ScenarioSpec(
                "jumlah baris salah",
                [
                    ScenarioStep(
                        "SELECT s.id AS nim FROM student AS s ORDER BY s.id LIMIT 2;",
                        expect_ok=True,
                        expect_columns=["nim"],
                        expect_rows=[[1]],
                    )
                ],
            ),
            ScenarioSpec(
                "data salah",
                [
                    ScenarioStep(
                        "SELECT s.id AS nim FROM student AS s ORDER BY s.id LIMIT 2;",
                        expect_ok=True,
                        expect_columns=["nim"],
                        expect_rows=[[1], [99]],
                    )
                ],
            ),
        ]

        report = self.driver.run_scenarios(self.qp, scenarios)
        self.assertEqual((report.total, report.passed, report.failed), (3, 0, 3))
        self.assertIn("columns", report.outcomes[0].error_message)
        self.assertIn("rows", report.outcomes[1].error_message)
        self.assertIn("rows", report.outcomes[2].error_message)


if __name__ == "__main__":
    unittest.main()
