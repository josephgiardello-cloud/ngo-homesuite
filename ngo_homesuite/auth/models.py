from __future__ import annotations

import getpass
import sqlite3
from typing import Any

from .passwords import password_hash, verify_password
from . import session
from .. import config
from ..domain.cli import cli_actor, cli_services
from ..domain.identity import Identity
from ..domain.store import DomainError
from ..prompts import prompt_non_empty
from ..db.connection import run_db
from .session import require_role


def _password_hash(password: str, salt: bytes) -> bytes:
    return password_hash(password, salt)


def _verify_password(password: str, salt_hex: str, expected_hash_hex: str) -> bool:
    """Legacy/compat alias for verify_password()."""

    return verify_password(password=password, salt_hex=salt_hex, expected_hash_hex=expected_hash_hex)


def create_user(conn: Any, cur: Any, username: str, password: str, role: str) -> None:
    try:
        Identity(cli_services()).create_user(
            config.TENANT, username, password, role,
            actor=cli_actor() if session.CURRENT_USER is not None else None,
        )
    except DomainError as exc:
        raise ValueError(str(exc)) from exc


def _create_user(conn: Any, cur: Any, username: str, password: str, role: str) -> None:
    """Legacy/compat alias for create_user()."""

    return create_user(conn=conn, cur=cur, username=username, password=password, role=role)


def ensure_admin_user(conn: Any, cur: Any) -> None:
    """Interactive bootstrap: create the first admin if there are no users."""

    cur.execute("SELECT COUNT(*) FROM users")
    count = int((cur.fetchone() or [0])[0] or 0)
    if count > 0:
        return

    print("\nNo users found. Create an initial admin account.")
    while True:
        username = prompt_non_empty("Admin username: ")
        pw1 = getpass.getpass("Admin password (min 10 chars): ")
        pw2 = getpass.getpass("Confirm password: ")
        if pw1 != pw2:
            print("Passwords do not match.")
            continue
        try:
            create_user(conn=conn, cur=cur, username=username, password=pw1, role="admin")
        except ValueError as e:
            print(e)
            continue
        print("Admin user created.")
        return


def _ensure_admin_user(conn: Any, cur: Any) -> None:
    """Legacy/compat alias for ensure_admin_user()."""

    return ensure_admin_user(conn=conn, cur=cur)


@require_role("admin")
def admin_create_user() -> None:
    username = prompt_non_empty("New username: ")
    role = prompt_non_empty("Role (admin/fundraiser/viewer): ").strip().lower()
    pw1 = getpass.getpass("Password (min 10 chars): ")
    pw2 = getpass.getpass("Confirm password: ")
    if pw1 != pw2:
        print("Passwords do not match.")
        return

    def op(conn: Any, cur: Any) -> None:
        create_user(conn=conn, cur=cur, username=username, password=pw1, role=role)

    try:
        run_db(op, write=True)
    except ValueError as e:
        print(e)
        return
    except sqlite3.Error as e:
        print(f"Could not create user: {e}")
        return

    print("User created.")
