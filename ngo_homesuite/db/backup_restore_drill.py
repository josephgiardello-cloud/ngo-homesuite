from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def _count_tables(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchone()
    return int(row[0] if row else 0)


def run_backup_restore_drill(db_path: str, output_dir: str) -> dict[str, str | int]:
    source = Path(db_path).resolve()
    if not source.exists():
        raise FileNotFoundError(f"Database not found: {source}")

    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    stamp = _utc_stamp()
    backup_path = out_dir / f"backup-{stamp}.sqlite3"
    restored_path = out_dir / f"restored-{stamp}.sqlite3"

    shutil.copy2(source, backup_path)
    shutil.copy2(backup_path, restored_path)

    with sqlite3.connect(str(source)) as src_conn:
        source_tables = _count_tables(src_conn)

    with sqlite3.connect(str(restored_path)) as restored_conn:
        integrity_row = restored_conn.execute("PRAGMA integrity_check;").fetchone()
        restored_tables = _count_tables(restored_conn)

    integrity = str(integrity_row[0] if integrity_row else "")
    if integrity.lower() != "ok":
        raise RuntimeError(f"Integrity check failed for restored DB: {integrity}")

    if source_tables != restored_tables:
        raise RuntimeError(
            f"Table count mismatch after restore: source={source_tables}, restored={restored_tables}"
        )

    return {
        "source": str(source),
        "backup": str(backup_path),
        "restored": str(restored_path),
        "integrity": integrity,
        "table_count": source_tables,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a SQLite backup/restore drill")
    parser.add_argument("--db", required=True, help="Path to source SQLite database")
    parser.add_argument(
        "--output-dir",
        default="backups/drills",
        help="Directory where backup and restored copies are written",
    )
    args = parser.parse_args()

    result = run_backup_restore_drill(args.db, args.output_dir)
    print("Backup/restore drill passed")
    print(f"source={result['source']}")
    print(f"backup={result['backup']}")
    print(f"restored={result['restored']}")
    print(f"integrity={result['integrity']}")
    print(f"table_count={result['table_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
