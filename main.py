"""Bootstrap mDBMS CoachDB: susun komponen sesuai urutan startup."""

from __future__ import annotations

import argparse
import sys

from common import config
from common.interfaces import QueryProcessor
from common.result import ExecutionResult
from common.transaction import ProtocolType
from concurrency_control import CCManager
from failure_recovery import FailureRecoveryManager
from query_optimizer import QueryOptimizerFacade
from query_processor.facade import QueryProcessorFacade
from storage_manager.facade import StorageManager

PROTOCOLS = {"lock": ProtocolType.LOCK_BASED, "timestamp": ProtocolType.TIMESTAMP_BASED}

DEMO_QUERIES = [
    "SELECT * FROM student;",
    "BEGIN;",
    "SELECT * FROM course;",
    "ABORT;",
    "EXPLAIN SELECT * FROM enrollment;",
    "SELECT * FROM dosen;",
    "SELECT name FROM student;",
]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Argumen baris perintah."""
    parser = argparse.ArgumentParser(description="mDBMS CoachDB")
    parser.add_argument("--protocol", choices=sorted(PROTOCOLS), default=config.DEFAULT_PROTOCOL,
                        help="protokol concurrency control")
    parser.add_argument("--data-dir", default=config.DATA_DIR, help="direktori data")
    parser.add_argument("--demo", action="store_true", help="jalankan query contoh lalu keluar")
    return parser.parse_args(argv)


def bootstrap(args: argparse.Namespace) -> QueryProcessor:
    """Bangun SM, FRM, CCM, QO, QP dan jalankan recovery. Exit 1 jika gagal."""
    sm = StorageManager(args.data_dir)
    frm = FailureRecoveryManager()
    ccm = CCManager(frm)
    frm.set_dependencies(sm, ccm)
    sm.set_log_flusher(frm.flush_log)

    result = frm.recover()
    if not result.ok:
        print(f"error: recovery gagal: {result.message}", file=sys.stderr)
        sys.exit(1)

    ccm.set_protocol(PROTOCOLS[args.protocol])
    print(f"Protokol aktif: {ccm.get_protocol().value}")
    ccm.initialize(result.last_tx_id)

    qo = QueryOptimizerFacade(sm)
    return QueryProcessorFacade(qo, sm, ccm, frm)


def print_result(sql: str, result: ExecutionResult) -> None:
    """Cetak ExecutionResult dalam bentuk tabel sederhana."""
    print(f"\n> {sql}")
    if not result.ok:
        print(f"ERROR: {result.message}")
        return
    if result.columns:
        table = [result.columns] + [["NULL" if v is None else str(v) for v in r] for r in result.rows]
        widths = [max(len(row[i]) for row in table) for i in range(len(result.columns))]
        for i, row in enumerate(table):
            print(" | ".join(v.ljust(w) for v, w in zip(row, widths)))
            if i == 0:
                print("-+-".join("-" * w for w in widths))
    if result.message:
        print(result.message)


def run_demo(qp: QueryProcessor) -> None:
    """Jalankan DEMO_QUERIES dalam satu sesi."""
    session = qp.open_session()
    for sql in DEMO_QUERIES:
        print_result(sql, qp.execute_query(sql, session))
    qp.close_session(session.session_id)


def main(argv: list[str] | None = None) -> None:
    """Entry point."""
    args = parse_args(argv)
    qp = bootstrap(args)
    if args.demo:
        run_demo(qp)
    else:
        print("Bootstrap selesai. Server dan CLI disediakan group QP; pakai --demo untuk contoh query.")


if __name__ == "__main__":
    main()
