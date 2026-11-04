"""Pemformat hasil eksekusi query dan pesan error untuk CLI (QP-01)."""

from __future__ import annotations

from common.errors import (
    LogError,
    MdbmsError,
    QuerySyntaxError,
    SemanticError,
    StorageError,
    TransactionAbortedError,
)
from common.result import ExecutionResult

QueryResult = ExecutionResult

__all__ = ["ExecutionResult", "QueryResult", "ResultFormatter"]


class ResultFormatter:
    """Memformat ExecutionResult dan exception menjadi teks ramah tanpa stack trace."""

    @staticmethod
    def format_value(value: object) -> str:
        """Ubah satu nilai sel menjadi representasi tampilan tabel."""
        if value is None:
            return "NULL"
        if isinstance(value, float):
            return repr(value)
        return str(value)

    def format(self, result: ExecutionResult) -> str:
        """Format ExecutionResult menjadi tabel ASCII (SELECT) atau pesan status/error."""
        if not result.ok:
            return self.format_error(result.message)

        if result.columns:
            headers = [str(col) for col in result.columns]
            formatted_rows = [
                [self.format_value(val) for val in row]
                for row in result.rows
            ]
            num_cols = len(headers)
            widths = [len(h) for h in headers]
            for row in formatted_rows:
                for idx in range(min(num_cols, len(row))):
                    if len(row[idx]) > widths[idx]:
                        widths[idx] = len(row[idx])

            lines: list[str] = []
            header_line = " | ".join(headers[i].ljust(widths[i]) for i in range(num_cols))
            separator_line = "-+-".join("-" * widths[i] for i in range(num_cols))
            lines.append(header_line)
            lines.append(separator_line)

            for row in formatted_rows:
                padded_cells = [
                    (row[i] if i < len(row) else "NULL").ljust(widths[i])
                    for i in range(num_cols)
                ]
                lines.append(" | ".join(padded_cells))

            row_count_text = f"({len(formatted_rows)} baris)"
            lines.append(row_count_text)
            if result.message and result.message != row_count_text:
                lines.append(result.message)
            return "\n".join(lines)

        if result.message:
            return result.message

        return f"{result.affected_rows} baris terpengaruh"

    def format_error(self, error: Exception | str) -> str:
        """Format exception atau pesan gagal menjadi string ERROR ramah."""
        if isinstance(error, QuerySyntaxError):
            return self._ensure_prefix(error.message, "ERROR [SYNTAX]")
        if isinstance(error, SemanticError):
            return self._ensure_prefix(error.message, "ERROR [SEMANTIC]")
        if isinstance(error, TransactionAbortedError):
            msg = error.message or "alasan tidak diketahui"
            if msg.upper().startswith("ERROR"):
                return msg
            return f"ERROR [ABORTED]: Transaksi {error.tx_id} dibatalkan ({msg})"
        if isinstance(error, StorageError):
            code = "SCHEMA" if error.__class__.__name__ == "SchemaViolationError" else "STORAGE"
            return self._ensure_prefix(error.message, f"ERROR [{code}]")
        if isinstance(error, LogError):
            return self._ensure_prefix(error.message, "ERROR [LOG]")
        if isinstance(error, MdbmsError):
            if error.__class__.__name__ == "PlanError":
                return self._ensure_prefix(error.message, "ERROR [PLAN]")
            return self._ensure_prefix(error.message, "ERROR")
        if isinstance(error, Exception):
            return f"ERROR [INTERNAL]: {error}"

        text = str(error).strip()
        if not text:
            return "ERROR: Eksekusi query gagal"
        if text.upper().startswith("ERROR"):
            return text
        return f"ERROR: {text}"

    @staticmethod
    def _ensure_prefix(message: str, prefix: str) -> str:
        cleaned = (message or "").strip()
        if not cleaned:
            return f"{prefix}: Terjadi kesalahan"
        if cleaned.upper().startswith("ERROR"):
            return cleaned
        return f"{prefix}: {cleaned}"
