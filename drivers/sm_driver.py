"""Driver Storage Manager: fungsi kosong."""

from __future__ import annotations

from common.interfaces.storage_manager import StorageManagerAPI


class SMDriver:

    def __init__(self, sm: StorageManagerAPI | None = None, data_dir: str | None = None) -> None:
        self._sm: StorageManagerAPI | None = sm
        self._data_dir: str | None = data_dir

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
