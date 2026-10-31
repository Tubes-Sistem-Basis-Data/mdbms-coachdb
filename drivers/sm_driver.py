"""Driver Storage Manager: fungsi kosong."""

from __future__ import annotations


class SMDriver:
    """Driver sementara komponen Storage Manager."""

    def __init__(self, sm=None, data_dir: str | None = None) -> None:
        self._sm = sm
        self._data_dir = data_dir

    def run_all(self) -> None:
        return None

    def run_scenario(self, name: str) -> bool:
        return False

    def print_summary(self) -> None:
        return None


def main(argv: list[str] | None = None) -> int:
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
