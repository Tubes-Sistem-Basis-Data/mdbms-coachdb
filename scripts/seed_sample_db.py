from __future__ import annotations

import argparse

from common import config
from storage_manager.catalog import Catalog


class SampleDatabaseSeeder:
    SEED: int = 3140

    def seed(self, data_dir: str, force: bool) -> None:
        raise NotImplementedError

    def _create_tables(self, catalog: Catalog) -> None:
        raise NotImplementedError

    def _generate_rows(self, table: str) -> list[tuple]:
        raise NotImplementedError


def main() -> None:
    parser = argparse.ArgumentParser(description="Isi basis data contoh CoachDB.")
    parser.add_argument("--data-dir", default=config.DATA_DIR)
    parser.add_argument("--force", action="store_true", help="timpa database yang sudah ada")
    args = parser.parse_args()
    SampleDatabaseSeeder().seed(args.data_dir, args.force)


if __name__ == "__main__":
    main()