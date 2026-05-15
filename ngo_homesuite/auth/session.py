from __future__ import annotations

import getpass
import sqlite3
import time
from typing import Any

from ..config import LOGIN_BACKOFF_BASE_SECONDS, MAX_LOGIN_ATTEMPTS
from ..db.connection import run_db
from ..prompts import prompt_non_empty


class AuthError(Exception):
    pass


CURRENT_USER: dict[str, Any] | None = None


def require_role(*roles: str):
    def decorator(fn):
        def wrapper(*args, **kwargs):
            if CURRENT_USER is None or CURRENT_USER.get("role") not in roles:
                print("Access denied.")
                return
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def login() -> dict[str, Any]:
    # Local import avoids circular dependency with auth.models
    from .models import verify_password

    for attempt in range(MAX_LOGIN_ATTEMPTS):
        username = prompt_non_empty("Username: ")
        password = getpass.getpass("Password: ")

        def op(_conn: Any, cur: Any):
            cur.execute(
                "SELECT id, username, password_salt, password_hash, role FROM users WHERE username = ?",
                (username,),
            )
            return cur.fetchone()

        row = run_db(op)
        if not row:
            print("Invalid username or password.")
            time.sleep(LOGIN_BACKOFF_BASE_SECONDS * (2**attempt))
            continue

        user_id, uname, salt_hex, hash_hex, role = row
        if not verify_password(password, salt_hex, hash_hex):
            print("Invalid username or password.")
            time.sleep(LOGIN_BACKOFF_BASE_SECONDS * (2**attempt))
            continue

        return {"id": int(user_id), "username": str(uname), "role": str(role)}

    raise AuthError("Too many failed login attempts")
