"""Simple role-based decorators for Flask routes."""

from functools import wraps
from flask import abort
from flask_login import current_user


def roles_required(*roles):
    """Require an authenticated user with one of the allowed roles."""

    allowed = set(roles)

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(403)
            if not current_user.has_role(*allowed):
                abort(403)
            return view_func(*args, **kwargs)

        return wrapped

    return decorator
