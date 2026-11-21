"""Permanent crash-restart integration tests through main.bootstrap."""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from common.catalog import ColumnSchema, DataType
from common.log import LogKind
import main
from storage_manager.facade import StorageManager

REPO_ROOT = Path(__file__).resolve().parents[2]
SUBPROCESS_TIMEOUT_SECONDS = 30
PROTOCOLS = ("lock", "timestamp")

_CRASH_WORKER = """
import json
import os
import sys

import main

payload = json.loads(sys.stdin.read())
args = main.parse_args(
    ["--data-dir", payload["data_dir"], "--protocol", payload["protocol"]]
)
qp = main.bootstrap(args)
session = qp.open_session()
for sql in payload["sqls"]:
    result = qp.execute_query(sql, session)
    if not result.ok:
        sys.stderr.write(f"SQL failed: {sql!r} -> {result.message}\\n")
        sys.exit(2)
if payload["flush_sm"]:
    qp._sm.flush()
sys.stdout.flush()
sys.stderr.flush()
os._exit(0)
"""


def prepare_data_dir(
    data_dir: str | Path,
    initial_rows: list[list] | None = None,
) -> None:
    """Create the student table (and optional initial rows), then close SM."""
    sm = StorageManager(str(data_dir))
    try:
        columns = (
            ColumnSchema("id", DataType.INTEGER, nullable=False),
            ColumnSchema("name", DataType.VARCHAR, length=50),
        )
        # TODO: use StorageManager.create_table when its public API is implemented.
        sm._catalog.create_table("student", columns, ("id",), ())
        if initial_rows:
            sm.bulk_load("student", initial_rows)
        sm.flush()
    finally:
        sm.close()


def run_crash(
    data_dir: str | Path,
    protocol: str,
    sqls: list[str],
    flush_sm: bool,
) -> subprocess.CompletedProcess[str]:
    """Run SQLs in a subprocess via main.bootstrap and crash with os._exit(0)."""
    payload = json.dumps(
        {
            "data_dir": str(data_dir),
            "protocol": protocol,
            "sqls": list(sqls),
            "flush_sm": bool(flush_sm),
        }
    )
    proc = subprocess.run(
        [sys.executable, "-c", _CRASH_WORKER],
        input=payload,
        text=True,
        capture_output=True,
        cwd=str(REPO_ROOT),
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"Crash subprocess failed (rc={proc.returncode})\n"
            f"stdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr}"
        )
    return proc


def bootstrap_runtime(data_dir: str | Path, protocol: str):
    """Bootstrap CoachDB runtime via main.bootstrap without noisy stdout."""
    args = main.parse_args(["--data-dir", str(data_dir), "--protocol", protocol])
    with contextlib.redirect_stdout(io.StringIO()):
        return main.bootstrap(args)


class TestCrashRestartBootstrap(unittest.TestCase):
    def _select_student_rows(self, qp) -> list[list]:
        session = qp.open_session()
        try:
            result = qp.execute_query("SELECT id, name FROM student;", session)
            self.assertTrue(result.ok, result.message)
            return sorted((list(row) for row in result.rows), key=lambda row: row[0])
        finally:
            qp.close_session(session.session_id)

    def _max_wal_tx_id(self, qp) -> int:
        tx_ids = [record.tx_id for record in qp._frm.read_log() if record.tx_id is not None]
        self.assertTrue(tx_ids, "Expected at least one transaction record in WAL")
        return max(tx_ids)

    def test_committed_redo_after_crash(self) -> None:
        for protocol in PROTOCOLS:
            with self.subTest(protocol=protocol):
                with TemporaryDirectory(prefix=f"coachdb-crash-redo-{protocol}-") as tmpdir:
                    prepare_data_dir(tmpdir, initial_rows=[[1, "Initial"]])
                    run_crash(
                        data_dir=tmpdir,
                        protocol=protocol,
                        sqls=[
                            "BEGIN;",
                            "INSERT INTO student VALUES (2, 'Committed');",
                            "COMMIT;",
                        ],
                        flush_sm=False,
                    )

                    qp = bootstrap_runtime(tmpdir, protocol)
                    try:
                        rows = self._select_student_rows(qp)
                        self.assertEqual(rows, [[1, "Initial"], [2, "Committed"]])

                        prev_max_tx_id = self._max_wal_tx_id(qp)
                        session = qp.open_session()
                        try:
                            begin_res = qp.execute_query("BEGIN;", session)
                            self.assertTrue(begin_res.ok, begin_res.message)
                            self.assertIsNotNone(session.tx_id)
                            self.assertGreater(session.tx_id, prev_max_tx_id)
                            abort_res = qp.execute_query("ABORT;", session)
                            self.assertTrue(abort_res.ok, abort_res.message)
                        finally:
                            qp.close_session(session.session_id)
                    finally:
                        main.close_runtime(qp)

    def test_uncommitted_undo_after_crash(self) -> None:
        for protocol in PROTOCOLS:
            with self.subTest(protocol=protocol):
                with TemporaryDirectory(prefix=f"coachdb-crash-undo-{protocol}-") as tmpdir:
                    prepare_data_dir(tmpdir, initial_rows=[[1, "Initial"]])
                    run_crash(
                        data_dir=tmpdir,
                        protocol=protocol,
                        sqls=[
                            "BEGIN;",
                            "INSERT INTO student VALUES (2, 'Uncommitted');",
                        ],
                        flush_sm=True,
                    )

                    qp = bootstrap_runtime(tmpdir, protocol)
                    try:
                        rows = self._select_student_rows(qp)
                        self.assertEqual(rows, [[1, "Initial"]])

                        records = qp._frm.read_log()
                        self.assertTrue(
                            any(record.kind is LogKind.ABORT for record in records),
                            "Expected recovery to append ABORT for uncommitted transaction",
                        )

                        prev_max_tx_id = self._max_wal_tx_id(qp)
                        session = qp.open_session()
                        try:
                            begin_res = qp.execute_query("BEGIN;", session)
                            self.assertTrue(begin_res.ok, begin_res.message)
                            self.assertIsNotNone(session.tx_id)
                            self.assertGreater(session.tx_id, prev_max_tx_id)
                            commit_res = qp.execute_query("COMMIT;", session)
                            self.assertTrue(commit_res.ok, commit_res.message)
                        finally:
                            qp.close_session(session.session_id)
                    finally:
                        main.close_runtime(qp)

    def test_ccm_advances_tx_id_past_recovered_last_tx_id(self) -> None:
        for protocol in PROTOCOLS:
            with self.subTest(protocol=protocol):
                with TemporaryDirectory(prefix=f"coachdb-crash-txid-{protocol}-") as tmpdir:
                    prepare_data_dir(tmpdir)
                    run_crash(
                        data_dir=tmpdir,
                        protocol=protocol,
                        sqls=[
                            "BEGIN;",
                            "INSERT INTO student VALUES (1, 'First');",
                            "COMMIT;",
                            "BEGIN;",
                            "INSERT INTO student VALUES (2, 'Second');",
                            "COMMIT;",
                            "BEGIN;",
                            "INSERT INTO student VALUES (3, 'Dropped');",
                        ],
                        flush_sm=True,
                    )

                    qp = bootstrap_runtime(tmpdir, protocol)
                    try:
                        self.assertEqual(
                            self._select_student_rows(qp),
                            [[1, "First"], [2, "Second"]],
                        )
                        recovered_max_tx_id = self._max_wal_tx_id(qp)
                        self.assertGreaterEqual(recovered_max_tx_id, 3)

                        session = qp.open_session()
                        try:
                            self.assertTrue(qp.execute_query("BEGIN;", session).ok)
                            self.assertIsNotNone(session.tx_id)
                            self.assertGreater(session.tx_id, recovered_max_tx_id)
                            ins = qp.execute_query(
                                "INSERT INTO student VALUES (4, 'AfterRestart');",
                                session,
                            )
                            self.assertTrue(ins.ok, ins.message)
                            self.assertTrue(qp.execute_query("COMMIT;", session).ok)
                        finally:
                            qp.close_session(session.session_id)

                        self.assertEqual(
                            self._select_student_rows(qp),
                            [[1, "First"], [2, "Second"], [4, "AfterRestart"]],
                        )
                    finally:
                        main.close_runtime(qp)


if __name__ == "__main__":
    unittest.main()
