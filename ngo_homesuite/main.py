from __future__ import annotations

from typing import Any

from ngo_homesuite.auth import session
from ngo_homesuite.auth.models import _ensure_admin_user
from ngo_homesuite.auth.session import AuthError, login
from ngo_homesuite.db.connection import run_db
from ngo_homesuite.db.schema import init_db
from ngo_homesuite.ui.menu import main_menu
from ngo_homesuite.utils.backup import _backup_reminder_on_startup


def main() -> None:
    init_db()

    run_db(lambda conn, cur: _ensure_admin_user(conn, cur), write=True)
    _backup_reminder_on_startup()

    try:
        session.CURRENT_USER = login()
    except AuthError:
        raise SystemExit(1)

    main_menu()


if __name__ == "__main__":
    main()
