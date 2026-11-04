"""Unit test komprehensif untuk QP-01: Client CLI, ResultFormatter, SessionManager, dan QueryProcessorFacade."""

from __future__ import annotations

import io
import threading
import time
import unittest
from typing import Iterator

from client import ClientCLI, StatementSplitter
from common.catalog import ColumnSchema, DataType, IndexInfo, TableSchema, TableStats
from common.errors import (
    LogError,
    MdbmsError,
    QuerySyntaxError,
    SemanticError,
    StorageError,
    TransactionAbortedError,
)
from common.expression import ColumnRef, Literal
from common.interfaces import (
    ConcurrencyControlManager,
    FailureRecoveryManager,
    QueryOptimizer,
    Session,
    StorageManager,
)
from common.log import (
    CheckpointResult,
    LogKind,
    LogRecord,
    LogResult,
    RecoveryResult,
    RecoveryTarget,
)
from common.plan import (
    CreateIndex,
    Delete,
    Insert,
    ParsedQuery,
    PlanNode,
    Project,
    QueryKind,
    TableScan,
    TransactionCommand,
    Update,
)
from common.result import ExecutionResult
from common.storage import RID, Row
from common.transaction import (
    AccessResponse,
    AccessStatus,
    Action,
    DataChange,
    ProtocolType,
    ResourceId,
    TxResult,
    TxStatus,
)
from query_processor.context import ExecContext
from query_processor.facade import MetaCommandHandler, QueryProcessorFacade
from query_processor.operators.base import Executable, RowOperator
from query_processor.result import ResultFormatter
from query_processor.server import QueryServer
from query_processor.session import SessionManager
from query_processor.transactions import TransactionCoordinator
from query_processor.translator import PlanTranslator
from query_processor.tuple import ExecRow, OutputSchema


class _StubStorageManager(StorageManager):
    def list_tables(self) -> list[str]:
        return ["student"]

    def get_schema(self, table: str) -> TableSchema:
        if table != "student":
            raise SemanticError(f"Tabel '{table}' tidak ditemukan", object_name=table)
        return TableSchema(
            name="student",
            columns=[
                ColumnSchema("id", DataType.INTEGER),
                ColumnSchema("name", DataType.VARCHAR, max_length=50),
                ColumnSchema("gpa", DataType.FLOAT),
            ],
        )

    def get_stats(self, table: str) -> TableStats:
        return TableStats(table=table, n_r=2, b_r=1, l_r=32.0, f_r=10, distinct={})

    def scan(self, table: str) -> Iterator[Row]:
        return iter(())

    def fetch(self, table: str, rid: RID) -> Row | None:
        return None

    def allocate_rid(self, table: str) -> RID:
        return RID(table, 0, 0)

    def release_rid(self, rid: RID) -> None:
        return None

    def insert(self, table: str, rid: RID, values: list, lsn: int | None) -> None:
        return None

    def update(self, table: str, rid: RID, values: list, lsn: int | None) -> None:
        return None

    def delete(self, table: str, rid: RID, lsn: int | None) -> None:
        return None

    def delete_all(self, table: str, lsn: int | None) -> None:
        return None

    def create_index(self, info: IndexInfo) -> None:
        return None

    def index_lookup(self, table: str, column: str, key) -> list[RID]:
        return []

    def index_range(self, table: str, column: str, low, high) -> list[RID]:
        return []

    def flush(self, page_ids: list[int] | None = None) -> None:
        return None

    def set_log_flusher(self, fn) -> None:
        return None


class _StubCCM(ConcurrencyControlManager):
    def __init__(self) -> None:
        self._next_tx = 1
        self.begun: list[int] = []
        self.committed: list[int] = []
        self.aborted: list[int] = []
        self.initialized_with: int | None = None
        self.fail_commit: bool = False

    def begin_transaction(self) -> int:
        tx_id = self._next_tx
        self._next_tx += 1
        self.begun.append(tx_id)
        return tx_id

    def validate_object(
        self,
        tx_id: int,
        resource: ResourceId,
        action: Action,
        change: DataChange | None = None,
    ) -> AccessResponse:
        return AccessResponse(status=AccessStatus.ALLOWED, lsn=1)

    def commit(self, tx_id: int) -> TxResult:
        if self.fail_commit:
            return TxResult(tx_id=tx_id, status=TxStatus.FAILED, ok=False, message="Commit ditolak CCM")
        self.committed.append(tx_id)
        return TxResult(tx_id=tx_id, status=TxStatus.COMMITTED, ok=True, message=f"COMMIT (tx {tx_id})")

    def abort(self, tx_id: int) -> TxResult:
        self.aborted.append(tx_id)
        return TxResult(tx_id=tx_id, status=TxStatus.ABORTED, ok=True, message=f"ABORT (tx {tx_id})")

    def set_protocol(self, protocol: ProtocolType) -> None:
        return None

    def get_protocol(self) -> ProtocolType:
        return ProtocolType.LOCK_BASED

    def initialize(self, last_tx_id: int) -> None:
        self.initialized_with = last_tx_id

    def pause_new_transactions(self) -> None:
        return None

    def wait_until_idle(self) -> None:
        return None

    def resume_transactions(self) -> None:
        return None


class _StubFRM(FailureRecoveryManager):
    def __init__(self) -> None:
        self.logs: list[LogRecord] = [
            LogRecord(kind=LogKind.BEGIN, tx_id=1, lsn=1, timestamp=1000.0),
            LogRecord(kind=LogKind.COMMIT, tx_id=1, lsn=2, timestamp=1001.0),
        ]
        self.last_recover_target: RecoveryTarget | None = None

    def write_log(self, record: LogRecord) -> LogResult:
        return LogResult(ok=True, lsn=len(self.logs) + 1)

    def flush_log(self, up_to_lsn: int) -> bool:
        return True

    def rollback(self, tx_id: int) -> TxResult:
        return TxResult(tx_id=tx_id, status=TxStatus.ABORTED, ok=True)

    def checkpoint(self) -> CheckpointResult:
        return CheckpointResult(ok=True, lsn=10, message="CHECKPOINT selesai (lsn 10)")

    def recover(self) -> RecoveryResult:
        return RecoveryResult(ok=True, last_tx_id=5, last_lsn=10)

    def recover_to(self, target: RecoveryTarget) -> RecoveryResult:
        self.last_recover_target = target
        return RecoveryResult(ok=True, last_tx_id=3, last_lsn=6, message="RESTORE berhasil")

    def read_log(self, tx_id: int | None = None) -> list[LogRecord]:
        if tx_id is None:
            return list(self.logs)
        return [r for r in self.logs if r.tx_id == tx_id]

    def set_dependencies(self, sm: StorageManager, ccm: ConcurrencyControlManager) -> None:
        return None


class _StubQO(QueryOptimizer):
    def parse_query(self, sql: str) -> ParsedQuery:
        cleaned = sql.strip().rstrip(";").strip()
        upper = cleaned.upper()
        if upper == "BEGIN" or upper == "BEGIN TRANSACTION":
            return ParsedQuery(sql=sql, kind=QueryKind.TRANSACTION, command=TransactionCommand.BEGIN)
        if upper == "COMMIT":
            return ParsedQuery(sql=sql, kind=QueryKind.TRANSACTION, command=TransactionCommand.COMMIT)
        if upper == "ABORT":
            return ParsedQuery(sql=sql, kind=QueryKind.TRANSACTION, command=TransactionCommand.ABORT)
        if upper.startswith("SELECT * FROM DOSEN"):
            raise SemanticError("tabel 'dosen' tidak ada", object_name="dosen")
        if upper.startswith("SELECT EMPTY"):
            plan = Project(
                columns=[ColumnRef("student", "id"), ColumnRef("student", "name")],
                children=[TableScan(table="student")],
            )
            return ParsedQuery(sql=sql, kind=QueryKind.SELECT, plan=plan)
        if upper.startswith("SELECT ABORT"):
            plan = Project(columns=[ColumnRef("student", "id")], children=[TableScan(table="student")])
            return ParsedQuery(sql=sql, kind=QueryKind.SELECT, plan=plan)
        if upper.startswith("SELECT STORAGE_ERR"):
            plan = Project(columns=[ColumnRef("student", "id")], children=[TableScan(table="student")])
            return ParsedQuery(sql=sql, kind=QueryKind.SELECT, plan=plan)
        if upper.startswith("SELECT INTERNAL_ERR"):
            plan = Project(columns=[ColumnRef("student", "id")], children=[TableScan(table="student")])
            return ParsedQuery(sql=sql, kind=QueryKind.SELECT, plan=plan)
        if upper.startswith("SELECT"):
            plan = Project(
                columns=[
                    ColumnRef("student", "id"),
                    ColumnRef("student", "name"),
                    ColumnRef("student", "gpa"),
                ],
                children=[TableScan(table="student")],
            )
            return ParsedQuery(sql=sql, kind=QueryKind.SELECT, plan=plan)
        if upper.startswith("INSERT"):
            plan = Insert(
                table="student",
                values=[
                    Literal(1, DataType.INTEGER),
                    Literal("Andi;Budi", DataType.VARCHAR),
                    Literal(3.75, DataType.FLOAT),
                ],
            )
            return ParsedQuery(sql=sql, kind=QueryKind.INSERT, plan=plan)
        if upper.startswith("UPDATE"):
            plan = Update(
                table="student",
                column="gpa",
                expression=Literal(3.9, DataType.FLOAT),
                children=[TableScan(table="student")],
            )
            return ParsedQuery(sql=sql, kind=QueryKind.UPDATE, plan=plan)
        if upper.startswith("DELETE"):
            plan = Delete(table="student", children=[TableScan(table="student")])
            return ParsedQuery(sql=sql, kind=QueryKind.DELETE, plan=plan)
        if upper.startswith("CREATE INDEX"):
            plan = CreateIndex(index=IndexInfo(name="idx_student_id", table="student", column="id"))
            return ParsedQuery(sql=sql, kind=QueryKind.CREATE_INDEX, plan=plan)
        raise QuerySyntaxError(f"Perintah tidak dikenali: {cleaned}", position=0, token=cleaned.split()[0])

    def optimize_query(self, query: ParsedQuery) -> ParsedQuery:
        return query

    def get_cost(self, plan: PlanNode) -> float:
        return 1.0

    def explain(self, query: ParsedQuery) -> str:
        return f"Plan optimal untuk: {query.sql.strip()}"


class _FuncExecutable(Executable):
    def __init__(self, fn) -> None:
        self._fn = fn

    def execute(self, ctx: ExecContext) -> ExecutionResult:
        return self._fn(ctx)


class _StubTranslator(PlanTranslator):
    def validate(self, node: PlanNode) -> None:
        return None

    def build(self, parsed: ParsedQuery, ctx: ExecContext) -> Executable:
        upper = parsed.sql.strip().upper()
        if upper.startswith("SELECT EMPTY"):
            return _FuncExecutable(
                lambda _c: ExecutionResult(ok=True, columns=["id", "name"], rows=[])
            )
        if upper.startswith("SELECT ABORT"):
            def _raise_abort(c: ExecContext) -> ExecutionResult:
                raise TransactionAbortedError("deadlock terdeteksi", tx_id=c.tx_id)
            return _FuncExecutable(_raise_abort)
        if upper.startswith("SELECT STORAGE_ERR"):
            def _raise_storage(_c: ExecContext) -> ExecutionResult:
                raise StorageError("halaman rusak")
            return _FuncExecutable(_raise_storage)
        if upper.startswith("SELECT INTERNAL_ERR"):
            def _raise_internal(_c: ExecContext) -> ExecutionResult:
                raise RuntimeError("bug tak terduga")
            return _FuncExecutable(_raise_internal)
        if parsed.kind == QueryKind.SELECT:
            return _FuncExecutable(
                lambda _c: ExecutionResult(
                    ok=True,
                    columns=["id", "name", "gpa"],
                    rows=[[1, "Andi", 3.75], [2, None, 3.2]],
                )
            )
        if parsed.kind == QueryKind.INSERT:
            return _FuncExecutable(lambda _c: ExecutionResult(ok=True, affected_rows=1))
        if parsed.kind == QueryKind.UPDATE:
            return _FuncExecutable(lambda _c: ExecutionResult(ok=True, affected_rows=2))
        if parsed.kind == QueryKind.DELETE:
            return _FuncExecutable(lambda _c: ExecutionResult(ok=True, affected_rows=3))
        if parsed.kind == QueryKind.CREATE_INDEX:
            return _FuncExecutable(lambda _c: ExecutionResult(ok=True, affected_rows=0))
        return _FuncExecutable(lambda _c: ExecutionResult(ok=False, message="Plan tidak dikenal"))

    def build_rows(self, node: PlanNode, ctx: ExecContext) -> RowOperator:
        raise NotImplementedError


class TestStatementSplitter(unittest.TestCase):
    def test_single_statement(self) -> None:
        splitter = StatementSplitter()
        stmts = splitter.feed("SELECT * FROM student;")
        self.assertEqual(stmts, ["SELECT * FROM student;"])
        self.assertFalse(splitter.has_pending())

    def test_multiline_statement(self) -> None:
        splitter = StatementSplitter()
        self.assertEqual(splitter.feed("SELECT id, name"), [])
        self.assertTrue(splitter.has_pending())
        self.assertEqual(splitter.feed("FROM student"), [])
        self.assertTrue(splitter.has_pending())
        stmts = splitter.feed("WHERE gpa > 3.5;")
        self.assertEqual(stmts, ["SELECT id, name\nFROM student\nWHERE gpa > 3.5;"])
        self.assertFalse(splitter.has_pending())

    def test_semicolon_inside_single_quotes_not_split(self) -> None:
        splitter = StatementSplitter()
        stmts = splitter.feed("INSERT INTO student VALUES (1, 'Andi;Budi', 3.75);")
        self.assertEqual(stmts, ["INSERT INTO student VALUES (1, 'Andi;Budi', 3.75);"])
        self.assertFalse(splitter.has_pending())

    def test_escaped_quotes_and_multiline_string(self) -> None:
        splitter = StatementSplitter()
        self.assertEqual(splitter.feed("SELECT * FROM student WHERE name = 'O''Neill;Jr"), [])
        self.assertTrue(splitter.has_pending())
        stmts = splitter.feed("MasihString';")
        self.assertEqual(
            stmts,
            ["SELECT * FROM student WHERE name = 'O''Neill;Jr\nMasihString';"],
        )
        self.assertFalse(splitter.has_pending())

    def test_multiple_statements_in_one_line(self) -> None:
        splitter = StatementSplitter()
        stmts = splitter.feed("BEGIN; SELECT * FROM student; COMMIT;")
        self.assertEqual(stmts, ["BEGIN;", "SELECT * FROM student;", "COMMIT;"])
        self.assertFalse(splitter.has_pending())

    def test_sql_line_comment_with_semicolon_ignored(self) -> None:
        splitter = StatementSplitter()
        self.assertEqual(splitter.feed("-- komentar dengan titik koma ; di sini"), [])
        self.assertFalse(splitter.has_pending())
        stmts = splitter.feed("SELECT * FROM student;")
        self.assertEqual(stmts, ["SELECT * FROM student;"])


class TestResultFormatter(unittest.TestCase):
    def setUp(self) -> None:
        self.formatter = ResultFormatter()

    def test_format_select_table_with_null_and_float(self) -> None:
        res = ExecutionResult(
            ok=True,
            columns=["id", "name", "gpa"],
            rows=[[1, "Andi", 3.75], [2, None, 3.5]],
        )
        out = self.formatter.format(res)
        lines = out.splitlines()
        self.assertEqual(lines[0], "id | name | gpa ")
        self.assertEqual(lines[1], "---+------+-----")
        self.assertEqual(lines[2], "1  | Andi | 3.75")
        self.assertEqual(lines[3], "2  | NULL | 3.5 ")
        self.assertEqual(lines[4], "(2 baris)")

    def test_format_empty_select_shows_header_and_zero_rows(self) -> None:
        res = ExecutionResult(ok=True, columns=["id", "name"], rows=[])
        out = self.formatter.format(res)
        lines = out.splitlines()
        self.assertEqual(lines[0], "id | name")
        self.assertEqual(lines[1], "---+-----")
        self.assertEqual(lines[2], "(0 baris)")

    def test_format_dml_and_status(self) -> None:
        res_insert = ExecutionResult(ok=True, affected_rows=1, message="1 baris ditambahkan")
        self.assertEqual(self.formatter.format(res_insert), "1 baris ditambahkan")

        res_plain = ExecutionResult(ok=True, affected_rows=4)
        self.assertEqual(self.formatter.format(res_plain), "4 baris terpengaruh")

    def test_format_errors_friendly_no_stacktrace(self) -> None:
        self.assertIn(
            "ERROR [SYNTAX]",
            self.formatter.format_error(QuerySyntaxError("token tak dikenal")),
        )
        self.assertIn(
            "ERROR [SEMANTIC]",
            self.formatter.format_error(SemanticError("tabel tidak ada")),
        )
        self.assertIn(
            "ERROR [ABORTED]",
            self.formatter.format_error(TransactionAbortedError("deadlock", tx_id=7)),
        )
        self.assertIn(
            "ERROR [STORAGE]",
            self.formatter.format_error(StorageError("gagal baca blok")),
        )
        self.assertIn(
            "ERROR [LOG]",
            self.formatter.format_error(LogError("wal gagal flush")),
        )
        res_err = ExecutionResult(ok=False, message="tabel 'dosen' tidak ada")
        self.assertEqual(self.formatter.format(res_err), "ERROR: tabel 'dosen' tidak ada")


class TestSessionManager(unittest.TestCase):
    def test_session_lifecycle_and_thread_safety(self) -> None:
        mgr = SessionManager()
        created: list[Session] = []
        lock = threading.Lock()

        def _worker() -> None:
            s = mgr.open_session()
            with lock:
                created.append(s)

        threads = [threading.Thread(target=_worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        ids = [s.session_id for s in created]
        self.assertEqual(len(set(ids)), 10)
        self.assertEqual(len(mgr.active_sessions()), 10)

        first_id = ids[0]
        self.assertIsNotNone(mgr.get_session(first_id))
        closed = mgr.close_session(first_id)
        self.assertIsNotNone(closed)
        self.assertIsNone(mgr.get_session(first_id))
        self.assertEqual(len(mgr.active_sessions()), 9)


class TestQueryProcessorFacade(unittest.TestCase):
    def setUp(self) -> None:
        self.sm = _StubStorageManager()
        self.ccm = _StubCCM()
        self.frm = _StubFRM()
        self.qo = _StubQO()
        self.translator = _StubTranslator()
        self.qp = QueryProcessorFacade(
            qo=self.qo,
            sm=self.sm,
            ccm=self.ccm,
            frm=self.frm,
            translator=self.translator,
            transactions=TransactionCoordinator(self.ccm),
            meta=MetaCommandHandler(qo=self.qo, frm=self.frm, ccm=self.ccm),
        )

    def test_autocommit_select_and_dml(self) -> None:
        session = self.qp.open_session()

        sel = self.qp.execute_query("SELECT * FROM student;", session)
        self.assertTrue(sel.ok)
        self.assertEqual(sel.columns, ["id", "name", "gpa"])
        self.assertEqual(len(sel.rows), 2)
        self.assertIsNone(session.tx_id)
        self.assertFalse(session.in_explicit_tx)
        self.assertEqual(self.ccm.begun, [1])
        self.assertEqual(self.ccm.committed, [1])

        ins = self.qp.execute_query("INSERT INTO student VALUES (1, 'Andi;Budi', 3.75);", session)
        self.assertTrue(ins.ok)
        self.assertEqual(ins.affected_rows, 1)
        self.assertEqual(ins.message, "1 baris ditambahkan")

        upd = self.qp.execute_query("UPDATE student SET gpa = 3.9;", session)
        self.assertTrue(upd.ok)
        self.assertEqual(upd.affected_rows, 2)
        self.assertEqual(upd.message, "2 baris diubah")

        dele = self.qp.execute_query("DELETE FROM student;", session)
        self.assertTrue(dele.ok)
        self.assertEqual(dele.affected_rows, 3)
        self.assertEqual(dele.message, "3 baris dihapus")

        idx = self.qp.execute_query("CREATE INDEX idx_student_id ON student(id);", session)
        self.assertTrue(idx.ok)
        self.assertEqual(idx.message, "Index berhasil dibuat")

    def test_explicit_transaction_begin_commit_abort(self) -> None:
        session = self.qp.open_session()

        b1 = self.qp.execute_query("BEGIN;", session)
        self.assertTrue(b1.ok)
        self.assertTrue(session.in_explicit_tx)
        self.assertEqual(session.tx_id, 1)

        b2 = self.qp.execute_query("BEGIN;", session)
        self.assertFalse(b2.ok)
        self.assertIn("ERROR [TX]", b2.message)
        self.assertEqual(session.tx_id, 1)

        idx_in_tx = self.qp.execute_query("CREATE INDEX idx_student_id ON student(id);", session)
        self.assertFalse(idx_in_tx.ok)
        self.assertIn("ERROR [TX]", idx_in_tx.message)
        self.assertTrue(session.in_explicit_tx)

        upd = self.qp.execute_query("UPDATE student SET gpa = 3.9;", session)
        self.assertTrue(upd.ok)
        self.assertEqual(self.ccm.committed, [])

        c1 = self.qp.execute_query("COMMIT;", session)
        self.assertTrue(c1.ok)
        self.assertIsNone(session.tx_id)
        self.assertFalse(session.in_explicit_tx)
        self.assertEqual(self.ccm.committed, [1])

        c_without_tx = self.qp.execute_query("COMMIT;", session)
        self.assertFalse(c_without_tx.ok)
        self.assertIn("ERROR [TX]", c_without_tx.message)

        a_without_tx = self.qp.execute_query("ABORT;", session)
        self.assertFalse(a_without_tx.ok)
        self.assertIn("ERROR [TX]", a_without_tx.message)

    def test_syntax_and_semantic_errors_preserve_explicit_tx(self) -> None:
        session = self.qp.open_session()
        self.qp.execute_query("BEGIN;", session)
        active_tx = session.tx_id

        syn = self.qp.execute_query("SELEK * FROM student;", session)
        self.assertFalse(syn.ok)
        self.assertIn("ERROR [SYNTAX]", syn.message)
        self.assertTrue(session.in_explicit_tx)
        self.assertEqual(session.tx_id, active_tx)

        sem = self.qp.execute_query("SELECT * FROM dosen;", session)
        self.assertFalse(sem.ok)
        self.assertIn("ERROR [SEMANTIC]", sem.message)
        self.assertTrue(session.in_explicit_tx)
        self.assertEqual(session.tx_id, active_tx)

        ab = self.qp.execute_query("ABORT;", session)
        self.assertTrue(ab.ok)
        self.assertFalse(session.in_explicit_tx)
        self.assertIsNone(session.tx_id)

    def test_runtime_and_aborted_errors_reset_transaction(self) -> None:
        session = self.qp.open_session()

        self.qp.execute_query("BEGIN;", session)
        ab_res = self.qp.execute_query("SELECT ABORT FROM student;", session)
        self.assertFalse(ab_res.ok)
        self.assertIn("ERROR [ABORTED]", ab_res.message)
        self.assertIsNone(session.tx_id)
        self.assertFalse(session.in_explicit_tx)

        self.qp.execute_query("BEGIN;", session)
        tx_before = session.tx_id
        st_res = self.qp.execute_query("SELECT STORAGE_ERR FROM student;", session)
        self.assertFalse(st_res.ok)
        self.assertIn("ERROR [STORAGE]", st_res.message)
        self.assertIn(tx_before, self.ccm.aborted)
        self.assertIsNone(session.tx_id)
        self.assertFalse(session.in_explicit_tx)

        self.qp.execute_query("BEGIN;", session)
        tx_internal = session.tx_id
        with self.assertLogs("coachdb.qp", level="ERROR"):
            int_res = self.qp.execute_query("SELECT INTERNAL_ERR FROM student;", session)
        self.assertFalse(int_res.ok)
        self.assertIn("ERROR [INTERNAL]", int_res.message)
        self.assertIn(tx_internal, self.ccm.aborted)
        self.assertIsNone(session.tx_id)

    def test_close_session_aborts_uncommitted_transaction(self) -> None:
        session = self.qp.open_session()
        self.qp.execute_query("BEGIN;", session)
        tx_id = session.tx_id
        self.assertIsNotNone(tx_id)

        self.qp.close_session(session.session_id)
        self.assertIn(tx_id, self.ccm.aborted)

    def test_meta_commands_explain_checkpoint_show_log_restore(self) -> None:
        session = self.qp.open_session()

        exp = self.qp.execute_query("EXPLAIN SELECT * FROM student;", session)
        self.assertTrue(exp.ok)
        self.assertIn("Plan optimal", exp.message)

        cp = self.qp.execute_query("CHECKPOINT;", session)
        self.assertTrue(cp.ok)
        self.assertIn("CHECKPOINT", cp.message)

        logs_all = self.qp.execute_query("SHOW LOG;", session)
        self.assertTrue(logs_all.ok)
        self.assertEqual(len(logs_all.rows), 2)

        logs_tx1 = self.qp.execute_query("SHOW LOG 1;", session)
        self.assertTrue(logs_tx1.ok)
        self.assertEqual(len(logs_tx1.rows), 2)

        rest_tx = self.qp.execute_query("RESTORE TO TX 1;", session)
        self.assertTrue(rest_tx.ok)
        self.assertIsNotNone(self.frm.last_recover_target)
        self.assertEqual(self.frm.last_recover_target.tx_id, 1)
        self.assertEqual(self.ccm.initialized_with, 3)

        rest_time = self.qp.execute_query("RESTORE TO TIME '2026-11-04 12:00:00';", session)
        self.assertTrue(rest_time.ok)
        self.assertIsNotNone(self.frm.last_recover_target)
        self.assertIsNotNone(self.frm.last_recover_target.timestamp)

        self.qp.execute_query("BEGIN;", session)
        cp_in_tx = self.qp.execute_query("CHECKPOINT;", session)
        self.assertFalse(cp_in_tx.ok)
        self.assertIn("ERROR [TX]", cp_in_tx.message)
        self.qp.execute_query("ABORT;", session)


class TestClientCLIAndServer(unittest.TestCase):
    def setUp(self) -> None:
        self.sm = _StubStorageManager()
        self.ccm = _StubCCM()
        self.frm = _StubFRM()
        self.qo = _StubQO()
        self.translator = _StubTranslator()
        self.qp = QueryProcessorFacade(
            qo=self.qo,
            sm=self.sm,
            ccm=self.ccm,
            frm=self.frm,
            translator=self.translator,
        )

    def test_cli_repl_multiline_error_recovery_and_exit(self) -> None:
        script = (
            "SELECT *\n"
            "FROM student;\n"
            "SELEK salah;\n"
            "INSERT INTO student VALUES (1, 'Andi;Budi', 3.75);\n"
            "exit\n"
        )
        in_stream = io.StringIO(script)
        out_stream = io.StringIO()

        cli = ClientCLI(qp=self.qp, input_stream=in_stream, output_stream=out_stream)
        cli.run()

        output = out_stream.getvalue()
        self.assertIn("id | name | gpa", output)
        self.assertIn("(2 baris)", output)
        self.assertIn("ERROR [SYNTAX]", output)
        self.assertIn("1 baris ditambahkan", output)

    def test_client_server_over_tcp_socket(self) -> None:
        server = QueryServer(self.qp, host="127.0.0.1", port=0)
        server_thread = threading.Thread(target=server.serve, daemon=True)
        server_thread.start()

        deadline = time.time() + 2.0
        while server.port == 0 and time.time() < deadline:
            time.sleep(0.01)

        try:
            script = (
                "BEGIN;\n"
                "SELECT * FROM student;\n"
                "SELECT * FROM dosen;\n"
                "COMMIT;\n"
                "quit\n"
            )
            in_stream = io.StringIO(script)
            out_stream = io.StringIO()

            cli = ClientCLI(
                host="127.0.0.1",
                port=server.port,
                input_stream=in_stream,
                output_stream=out_stream,
            )
            cli.run()

            output = out_stream.getvalue()
            self.assertIn("BEGIN (tx 1)", output)
            self.assertIn("Andi", output)
            self.assertIn("ERROR [SEMANTIC]: tabel 'dosen' tidak ada", output)
            self.assertIn("COMMIT (tx 1)", output)
        finally:
            server.shutdown()
            server_thread.join(timeout=2.0)


if __name__ == "__main__":
    unittest.main()
