from __future__ import annotations

import copy
import io
import unittest
from unittest.mock import Mock

from client import ClientCLI
from common.catalog import ColumnSchema, DataType, ForeignKey, TableSchema, TableStats
from common.errors import LogError, SemanticError, StorageError
from common.interfaces import ConcurrencyControlManager, Session, StorageManager
from common.plan import CreateTable, DropTable, ParsedQuery, QueryKind
from common.result import ExecutionResult
from common.storage import RID, Row
from common.transaction import AccessResponse, AccessStatus, TxResult, TxStatus
from query_optimizer import QueryOptimizerFacade
from query_processor.context import ExecContext
from query_processor.ddl import DDLCoordinator
from query_processor.errors import PlanError
from query_processor.facade import QueryProcessorFacade
from query_processor.operators.base import Executable
from query_processor.translator import PlanTranslator


class CatalogMutatingFakeSM(StorageManager):
    def __init__(self) -> None:
        self.schemas: dict[str, TableSchema] = {
            "student": TableSchema(
                "student",
                [
                    ColumnSchema("id", DataType.INTEGER, nullable=False),
                    ColumnSchema("name", DataType.VARCHAR, length=50),
                    ColumnSchema("gpa", DataType.FLOAT),
                ],
                primary_key=["id"],
            ),
            "enrollment": TableSchema(
                "enrollment",
                [
                    ColumnSchema("student_id", DataType.INTEGER),
                    ColumnSchema("course_id", DataType.INTEGER),
                ],
                foreign_keys=[
                    ForeignKey(
                        column="student_id",
                        ref_table="student",
                        ref_column="id",
                    )
                ],
            ),
        }
        self.rows: dict[str, list[Row]] = {
            "student": [
                Row(RID("student", 0, 0), [1, "Ada", 3.2]),
                Row(RID("student", 0, 1), [2, "Budi", 3.9]),
                Row(RID("student", 0, 2), [3, "Cici", 2.8]),
            ],
            "enrollment": [
                Row(RID("enrollment", 0, 0), [1, 101]),
                Row(RID("enrollment", 0, 1), [2, 102]),
            ],
        }
        self.create_calls: list[str] = []
        self.drop_calls: list[str] = []

    def list_tables(self) -> list[str]:
        return list(self.schemas.keys())

    def get_schema(self, table: str) -> TableSchema:
        if table not in self.schemas:
            raise SemanticError(f"Tabel '{table}' tidak ditemukan")
        return copy.deepcopy(self.schemas[table])

    def get_stats(self, table: str) -> TableStats:
        if table not in self.schemas:
            raise SemanticError(f"Tabel '{table}' tidak ditemukan")
        rows = self.rows.get(table, [])
        distinct = {col.name: max(1, len(rows)) for col in self.schemas[table].columns}
        return TableStats(
            table=table,
            n_r=len(rows),
            b_r=max(1, len(rows)),
            l_r=32.0,
            f_r=10,
            distinct=distinct,
        )

    def scan(self, table: str):
        if table not in self.schemas:
            raise SemanticError(f"Tabel '{table}' tidak ditemukan")
        return iter(copy.deepcopy(self.rows.get(table, [])))

    def fetch(self, table: str, rid: RID) -> Row | None:
        for row in self.rows.get(table, []):
            if row.rid == rid:
                return copy.deepcopy(row)
        return None

    def allocate_rid(self, table: str) -> RID:
        rows = self.rows.setdefault(table, [])
        return RID(table, 0, len(rows))

    def release_rid(self, rid: RID) -> None:
        return None

    def insert(self, table: str, rid: RID, values: list, lsn: int | None) -> None:
        self.rows.setdefault(table, []).append(Row(rid, list(values)))

    def update(self, table: str, rid: RID, values: list, lsn: int | None) -> None:
        rows = self.rows.get(table, [])
        for idx, row in enumerate(rows):
            if row.rid == rid:
                rows[idx] = Row(rid, list(values))
                return

    def delete(self, table: str, rid: RID, lsn: int | None) -> None:
        self.rows[table] = [r for r in self.rows.get(table, []) if r.rid != rid]

    def delete_all(self, table: str, lsn: int | None) -> None:
        self.rows[table] = []

    def validate(self, table: str, values: list, rid: RID | None = None) -> list:
        schema = self.get_schema(table)
        if len(values) != len(schema.columns):
            raise SemanticError("Jumlah nilai tidak sesuai skema")
        return list(values)

    def create_table(self, schema: TableSchema) -> None:
        self.create_calls.append(schema.name)
        if schema.name in self.schemas:
            raise SemanticError(f"Tabel '{schema.name}' sudah ada")
        self.schemas[schema.name] = copy.deepcopy(schema)
        self.rows[schema.name] = []

    def drop_table(self, table: str) -> None:
        self.drop_calls.append(table)
        if table not in self.schemas:
            raise SemanticError(f"Tabel '{table}' tidak ditemukan")
        del self.schemas[table]
        self.rows.pop(table, None)

    def create_index(self, info) -> None:
        return None

    def index_lookup(self, table: str, column: str, key) -> list[RID]:
        return []

    def index_range(self, table: str, column: str, low, high) -> list[RID]:
        return []

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None

    def list_indexes(self, table: str) -> list:
        return []

    def rebuild_indexes(self) -> None:
        return None

    def restore_from(self, src_dir: str) -> None:
        return None

    def set_log_flusher(self, flusher) -> None:
        return None

    def snapshot_to(self, dst_dir: str) -> None:
        return None


class _FallbackCreateTableExec(Executable):
    def __init__(self, schema: TableSchema, seen_tx_ids: list[int | None]) -> None:
        self._schema = schema
        self._seen_tx_ids = seen_tx_ids

    def execute(self, ctx: ExecContext) -> ExecutionResult:
        self._seen_tx_ids.append(ctx.tx_id)
        ctx.sm.create_table(self._schema)
        return ExecutionResult(ok=True, affected_rows=0, message=f"Tabel '{self._schema.name}' berhasil dibuat")


class _FallbackDropTableExec(Executable):
    def __init__(self, table: str, cascade: bool, seen_tx_ids: list[int | None]) -> None:
        self._table = table
        self._cascade = cascade
        self._seen_tx_ids = seen_tx_ids

    def execute(self, ctx: ExecContext) -> ExecutionResult:
        self._seen_tx_ids.append(ctx.tx_id)
        tables = ctx.sm.list_tables()
        if self._table not in tables:
            raise SemanticError(f"Tabel '{self._table}' tidak ditemukan")

        dependents: dict[str, list[str]] = {t: [] for t in tables}
        for name in tables:
            schema = ctx.sm.get_schema(name)
            for fk in schema.foreign_keys:
                if fk.ref_table in dependents and fk.ref_table != name:
                    dependents[fk.ref_table].append(name)

        if not self._cascade and dependents.get(self._table):
            refs = ", ".join(dependents[self._table])
            raise SemanticError(f"Tabel '{self._table}' masih dirujuk oleh {refs}")

        order: list[str] = []
        visited: set[str] = set()

        def visit(curr: str) -> None:
            if curr in visited:
                return
            visited.add(curr)
            for child in dependents.get(curr, []):
                visit(child)
            order.append(curr)

        visit(self._table)
        for target in order:
            ctx.sm.drop_table(target)
        return ExecutionResult(ok=True, affected_rows=0, message=f"Tabel '{self._table}' berhasil dihapus")


class DDLTestTranslator(PlanTranslator):
    def __init__(self) -> None:
        super().__init__()
        self.seen_ddl_tx_ids: list[int | None] = []

    def validate(self, node) -> None:
        if isinstance(node, (CreateTable, DropTable)):
            return
        super().validate(node)

    def build(self, parsed: ParsedQuery, ctx: ExecContext) -> Executable:
        try:
            res = super().build(parsed, ctx)
            if parsed.kind in (QueryKind.CREATE_TABLE, QueryKind.DROP_TABLE):
                self.seen_ddl_tx_ids.append(ctx.tx_id)
            return res
        except PlanError:
            if parsed.kind is QueryKind.CREATE_TABLE and isinstance(parsed.plan, CreateTable):
                return _FallbackCreateTableExec(parsed.plan.schema, self.seen_ddl_tx_ids)
            if parsed.kind is QueryKind.DROP_TABLE and isinstance(parsed.plan, DropTable):
                return _FallbackDropTableExec(parsed.plan.table, parsed.plan.cascade, self.seen_ddl_tx_ids)
            raise


class TestBonusFacade(unittest.TestCase):
    def setUp(self) -> None:
        self.sm = CatalogMutatingFakeSM()
        self.ccm = Mock(spec=ConcurrencyControlManager)
        self.ccm.begin_transaction.side_effect = list(range(1, 50))
        self.ccm.commit.side_effect = lambda tx: TxResult(tx, TxStatus.COMMITTED, True)
        self.ccm.abort.side_effect = lambda tx: TxResult(tx, TxStatus.ABORTED, True)
        self.ccm.validate_object.return_value = AccessResponse(AccessStatus.ALLOWED, lsn=1)
        self.qo = QueryOptimizerFacade(self.sm)
        self.translator = DDLTestTranslator()

    def make_facade(self, *, allow_ddl: bool = False) -> QueryProcessorFacade:
        qp = QueryProcessorFacade(
            qo=self.qo,
            sm=self.sm,
            ccm=self.ccm,
            translator=self.translator,
        )
        qp._ddl = DDLCoordinator(
            transactions=qp._transactions,
            allow_ddl=allow_ddl,
            formatter=qp._formatter,
            admin_lock=qp._ddl.admin_lock,
        )
        return qp

    def test_uncertain_commit_blocks_subsequent_data_and_ddl_without_new_transaction(self) -> None:
        qp = self.make_facade(allow_ddl=True)
        session = qp.open_session()
        self.ccm.commit.side_effect = lambda tx: TxResult(tx, TxStatus.FAILED, False, "status commit belum pasti")

        first = qp.execute_query("SELECT id FROM student ORDER BY id LIMIT 1;", session)
        self.assertFalse(first.ok)
        self.assertEqual(session.tx_id, 1)
        self.assertEqual(self.ccm.begin_transaction.call_count, 1)
        self.ccm.abort.assert_not_called()

        for follow_up in (
            "SELECT id FROM student;",
            "INSERT INTO enrollment VALUES (1, 200);",
            "CREATE TABLE note (id INTEGER PRIMARY KEY);",
            "BEGIN;",
            "COMMIT;",
            "ABORT;",
        ):
            with self.subTest(sql=follow_up):
                res = qp.execute_query(follow_up, session)
                self.assertFalse(res.ok)
                self.assertIn("belum pasti", res.message)
                self.assertEqual(session.tx_id, 1)
                self.assertEqual(self.ccm.begin_transaction.call_count, 1)
                self.assertEqual(self.sm.create_calls, [])

        other_session = qp.open_session()
        self.ccm.commit.side_effect = lambda tx: TxResult(tx, TxStatus.COMMITTED, True)
        ok_res = qp.execute_query("SELECT id FROM student ORDER BY id LIMIT 1;", other_session)
        self.assertTrue(ok_res.ok)
        self.assertEqual(ok_res.rows, [[1]])

    def test_disconnect_calls_abort_only_once_and_preserves_session_on_cleanup_failure(self) -> None:
        qp = self.make_facade()
        s1 = qp.open_session()
        self.assertTrue(qp.execute_query("BEGIN;", s1).ok)
        self.assertEqual(s1.tx_id, 1)

        qp.close_session(s1.session_id)
        qp.close_session(s1.session_id)
        self.ccm.abort.assert_called_once_with(1)
        self.assertIsNone(qp._sessions.get_session(s1.session_id))

        s2 = qp.open_session()
        self.assertTrue(qp.execute_query("BEGIN;", s2).ok)
        self.assertEqual(s2.tx_id, 2)
        self.ccm.abort.side_effect = LogError("WAL gagal ditulis")

        with self.assertLogs("coachdb.qp", level="ERROR"):
            qp.close_session(s2.session_id)

        self.assertIsNotNone(qp._sessions.get_session(s2.session_id))
        self.assertEqual(s2.tx_id, 2)

    def test_explicitly_disabled_ddl_rejected_before_calling_sm_or_starting_tx(self) -> None:
        qp = self.make_facade(allow_ddl=False)
        session = qp.open_session()

        for sql in (
            "CREATE TABLE note (id INTEGER PRIMARY KEY, student_id INTEGER REFERENCES student(id));",
            "DROP TABLE enrollment RESTRICT;",
        ):
            with self.subTest(sql=sql):
                res = qp.execute_query(sql, session)
                self.assertFalse(res.ok)
                self.assertIn("DDL", res.message)
                self.assertIsNone(session.tx_id)
                self.ccm.begin_transaction.assert_not_called()
                self.assertEqual(self.sm.create_calls, [])
                self.assertEqual(self.sm.drop_calls, [])

    def test_ddl_rejected_when_session_has_active_transaction(self) -> None:
        qp = self.make_facade(allow_ddl=True)
        session = qp.open_session()
        self.assertTrue(qp.execute_query("BEGIN;", session).ok)

        res = qp.execute_query("CREATE TABLE note (id INTEGER PRIMARY KEY);", session)
        self.assertFalse(res.ok)
        self.assertIn("transaksi aktif", res.message)
        self.assertEqual(session.tx_id, 1)
        self.assertTrue(session.in_explicit_tx)
        self.assertEqual(self.sm.create_calls, [])
        self.ccm.abort.assert_not_called()

    def test_ddl_execution_with_fake_sm_mutates_catalog_and_keeps_cli_alive_on_error(self) -> None:
        qp = self.make_facade(allow_ddl=True)
        session = qp.open_session()

        create_res = qp.execute_query(
            "CREATE TABLE note (id INTEGER PRIMARY KEY, student_id INTEGER REFERENCES student(id));",
            session,
        )
        self.assertTrue(create_res.ok, create_res.message)
        self.assertEqual(create_res.affected_rows, 0)
        self.assertIn("note", self.sm.list_tables())
        self.assertEqual(self.translator.seen_ddl_tx_ids, [None])
        self.ccm.begin_transaction.assert_not_called()

        restrict_res = qp.execute_query("DROP TABLE student RESTRICT;", session)
        self.assertFalse(restrict_res.ok)
        self.assertIn("student", self.sm.list_tables())

        cascade_res = qp.execute_query("DROP TABLE student CASCADE;", session)
        self.assertTrue(cascade_res.ok, cascade_res.message)
        self.assertEqual(cascade_res.affected_rows, 0)
        self.assertNotIn("student", self.sm.list_tables())
        self.assertNotIn("enrollment", self.sm.list_tables())
        self.assertNotIn("note", self.sm.list_tables())

        inp = io.StringIO(
            "DROP TABLE missing_table RESTRICT;\n"
            "CREATE TABLE dept (id INTEGER PRIMARY KEY);\n"
            "\\q\n"
        )
        out = io.StringIO()
        cli = ClientCLI(qp=qp, input_stream=inp, output_stream=out)
        cli.run()
        text = out.getvalue()
        self.assertIn("ERROR", text)
        self.assertIn("dept", self.sm.list_tables())

    def test_select_bonus_features_preserve_headers_and_rows_through_facade(self) -> None:
        qp = self.make_facade(allow_ddl=True)
        session = qp.open_session()

        limit_alias = qp.execute_query(
            "SELECT s.id AS nim, s.name AS nama FROM student AS s ORDER BY s.id LIMIT 2;",
            session,
        )
        self.assertTrue(limit_alias.ok, limit_alias.message)
        self.assertEqual(limit_alias.columns, ["nim", "nama"])
        self.assertEqual(limit_alias.rows, [[1, "Ada"], [2, "Budi"]])

        empty_alias = qp.execute_query(
            "SELECT s.id AS nim FROM student AS s WHERE s.id > 100 LIMIT 3;",
            session,
        )
        self.assertTrue(empty_alias.ok, empty_alias.message)
        self.assertEqual(empty_alias.columns, ["nim"])
        self.assertEqual(empty_alias.rows, [])

        self_join = qp.execute_query(
            "SELECT a.name, b.name FROM student a JOIN student b ON a.id = b.id ORDER BY a.id LIMIT 2;",
            session,
        )
        self.assertTrue(self_join.ok, self_join.message)
        self.assertEqual(self_join.columns, ["name", "name"])
        self.assertEqual(self_join.rows, [["Ada", "Ada"], ["Budi", "Budi"]])

        derived = qp.execute_query(
            "SELECT q.nim FROM (SELECT id AS nim FROM student ORDER BY id LIMIT 2) AS q ORDER BY q.nim;",
            session,
        )
        self.assertTrue(derived.ok, derived.message)
        self.assertEqual(derived.columns, ["nim"])
        self.assertEqual(derived.rows, [[1], [2]])

        scalar = qp.execute_query(
            "SELECT id FROM student WHERE gpa > (SELECT gpa FROM student WHERE id = 1) ORDER BY id;",
            session,
        )
        self.assertTrue(scalar.ok, scalar.message)
        self.assertEqual(scalar.columns, ["id"])
        self.assertEqual(scalar.rows, [[2]])


if __name__ == "__main__":
    unittest.main()
