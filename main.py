from __future__ import annotations

import argparse
import sys

from client import ClientCLI
from common import config
from common.interfaces import QueryProcessor
from common.result import ExecutionResult
from common.transaction import ProtocolType
from concurrency_control import CCManager
from failure_recovery import FailureRecoveryManager
from query_optimizer import QueryOptimizerFacade
from query_processor.facade import QueryProcessorFacade
from query_processor.result import ResultFormatter
from query_processor.server import QueryServer
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

_FORMATTER = ResultFormatter()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="mDBMS CoachDB")
    parser.add_argument(
        "--protocol",
        choices=sorted(PROTOCOLS),
        default=config.DEFAULT_PROTOCOL,
        help="protokol concurrency control",
    )
    parser.add_argument("--data-dir", default=config.DATA_DIR, help="direktori data")
    parser.add_argument("--host", default=config.HOST, help="alamat host server TCP")
    parser.add_argument("--port", type=int, default=config.PORT, help="port server TCP")
    parser.add_argument("--demo", action="store_true", help="jalankan query contoh lalu keluar")
    parser.add_argument("--cli", action="store_true", help="jalankan REPL CLI interaktif lokal")
    parser.add_argument("--serve", action="store_true", help="jalankan server TCP multi-client")
    return parser.parse_args(argv)


def bootstrap(args: argparse.Namespace) -> QueryProcessor:
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
    print(f"\n> {sql}")
    print(_FORMATTER.format(result))


def run_demo(qp: QueryProcessor) -> None:
    session = qp.open_session()
    try:
        for sql in DEMO_QUERIES:
            print_result(sql, qp.execute_query(sql, session))
    finally:
        qp.close_session(session.session_id)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    qp = bootstrap(args)
    if args.demo:
        run_demo(qp)
    elif args.cli:
        ClientCLI(qp=qp).run()
    elif args.serve:
        server = QueryServer(qp, host=args.host, port=args.port)
        print(f"Server CoachDB mendengarkan di {args.host}:{server.port}")
        try:
            server.serve()
        except KeyboardInterrupt:
            server.shutdown()
    else:
        print("Bootstrap selesai. Gunakan --demo, --cli, atau --serve.")


if __name__ == "__main__":
    main()
