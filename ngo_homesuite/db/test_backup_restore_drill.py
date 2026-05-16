from __future__ import annotations

import sqlite3
from pathlib import Path

from ngo_homesuite.db.backup_restore_drill import run_backup_restore_drill


def test_backup_restore_drill_creates_backup_and_restored_copy(tmp_path: Path):
    db_path = tmp_path / "source.sqlite3"
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("CREATE TABLE donors (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        conn.execute("INSERT INTO donors (name) VALUES ('Alice')")
        conn.commit()

    output_dir = tmp_path / "drills"
    result = run_backup_restore_drill(str(db_path), str(output_dir))

    assert Path(result["backup"]).exists()
    assert Path(result["restored"]).exists()
    assert result["integrity"] == "ok"
    assert int(result["table_count"]) == 1


def test_backup_restore_drill_raises_for_missing_source(tmp_path: Path):
    missing_db = tmp_path / "missing.sqlite3"

    try:
        run_backup_restore_drill(str(missing_db), str(tmp_path / "drills"))
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("Expected FileNotFoundError for missing source database")
