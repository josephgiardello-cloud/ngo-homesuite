from __future__ import annotations

import argparse
import os
from pathlib import Path

from flask_migrate import downgrade as alembic_downgrade
from flask_migrate import upgrade as alembic_upgrade

from ngo_homesuite.app_factory import create_app
from ngo_homesuite.db.migrate import auto_migrate


ALEMBIC_DIRECTORY = Path(__file__).resolve().parents[2] / "migrations"


def _sqlite_db_path(database_uri: str) -> str | None:
    normalized = str(database_uri or "").strip()
    if normalized.startswith("sqlite:///") and ":memory:" not in normalized:
        return normalized.replace("sqlite:///", "", 1)
    return None


def run_deploy_migration(action: str, *, revision: str | None = None) -> dict[str, str]:
    previous_role = os.environ.get("APP_PROCESS_ROLE")
    os.environ["APP_PROCESS_ROLE"] = "migrator"
    try:
        app = create_app()
        database_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", ""))
        sqlite_path = _sqlite_db_path(database_uri)
        if sqlite_path:
            if action != "upgrade":
                raise RuntimeError("SQLite deploy migration runner only supports upgrade; use backup/restore for rollback drills.")
            auto_migrate(sqlite_path)
            return {"backend": "sqlite", "action": action, "revision": "legacy-sql"}

        if not ALEMBIC_DIRECTORY.exists():
            raise RuntimeError(f"Alembic directory not found: {ALEMBIC_DIRECTORY}")

        selected_revision = revision or ("head" if action == "upgrade" else "-1")
        with app.app_context():
            if action == "upgrade":
                alembic_upgrade(directory=str(ALEMBIC_DIRECTORY), revision=selected_revision)
            else:
                alembic_downgrade(directory=str(ALEMBIC_DIRECTORY), revision=selected_revision)
        return {"backend": "alembic", "action": action, "revision": selected_revision}
    finally:
        if previous_role is None:
            os.environ.pop("APP_PROCESS_ROLE", None)
        else:
            os.environ["APP_PROCESS_ROLE"] = previous_role


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NGO HomeSuite deploy migration runner")
    parser.add_argument("action", choices=["upgrade", "downgrade"], help="Migration action to run")
    parser.add_argument("--revision", default=None, help="Alembic revision target (defaults: head for upgrade, -1 for downgrade)")
    args = parser.parse_args(argv)

    result = run_deploy_migration(args.action, revision=args.revision)
    print(f"Deploy migration complete: backend={result['backend']} action={result['action']} revision={result['revision']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())