"""Authentication/authorization decorators.

This module provides a stable import path for route modules that historically
used ``ngo_homesuite.auth.decorators``.
"""

from __future__ import annotations

from functools import wraps

from flask import abort
from flask_login import current_user


def roles_required(*roles: str):
    """Require an authenticated user with one of the allowed roles."""

    allowed = {r for r in roles if isinstance(r, str) and r.strip()}

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(403)
            if not current_user.has_role(*allowed):
                abort(403)
            return view_func(*args, **kwargs)

        wrapped.required_roles = tuple(sorted(allowed))
        return wrapped

    return decorator
