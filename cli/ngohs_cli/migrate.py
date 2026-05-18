"""CLI command: homesuite migrate.

This wrapper intentionally delegates to the core migrator so there is a single
authoritative migration path and a single audit trail.
"""

from __future__ import annotations

import argparse

from ngo_homesuite.db.migrate import auto_migrate


def cli_migrate(db_path: str | None = None) -> None:
    return auto_migrate(db_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NGO HomeSuite migrate CLI wrapper")
    parser.add_argument("--db-path", default=None, help="Override database path")
    args = parser.parse_args(argv)
    cli_migrate(args.db_path)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
