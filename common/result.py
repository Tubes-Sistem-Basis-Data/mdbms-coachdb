"""Hasil eksekusi query yang dikirim ke client."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class ExecutionResult:
    """Hasil `QP.execute_query`, bisa di-serialize ke JSON untuk socket."""

    ok: bool
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    affected_rows: int = 0
    message: str = ""

    def to_dict(self) -> dict:
        """Ubah ke dict yang aman untuk `json.dumps`."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ExecutionResult:
        """Bangun kembali dari dict hasil `to_dict`."""
        return cls(
            ok=bool(data["ok"]),
            columns=list(data.get("columns", [])),
            rows=[list(r) for r in data.get("rows", [])],
            affected_rows=int(data.get("affected_rows", 0)),
            message=str(data.get("message", "")),
        )
