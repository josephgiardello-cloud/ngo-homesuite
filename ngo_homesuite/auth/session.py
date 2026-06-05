from __future__ import annotations

import getpass
import sqlite3
import time
from typing import Any

from flask import has_app_context

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
    if has_app_context():
        raise RuntimeError("Legacy CLI auth session cannot run inside the Flask application runtime")

    # Local import avoids circular dependency with auth.models
    from .models import authenticate_user

    for attempt in range(MAX_LOGIN_ATTEMPTS):
        username = prompt_non_empty("Username: ")
        password = getpass.getpass("Password: ")

        try:
            return authenticate_user(username=username, password=password)
        except ValueError as exc:
            print(str(exc))
            time.sleep(LOGIN_BACKOFF_BASE_SECONDS * (2**attempt))
            continue

    raise AuthError("Too many failed login attempts")
