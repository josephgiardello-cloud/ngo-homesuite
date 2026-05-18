from __future__ import annotations

from functools import wraps
from typing import Any, Callable, TypeVar

F = TypeVar('F', bound=Callable[..., Any])


def enforce_error_contract(fn: F) -> F:
    """Mark a runtime-critical function as requiring explicit error classification.

    This decorator is intentionally lightweight for now; it gives us a uniform
    annotation surface and can be extended by CI/import-time checks later.
    """

    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return fn(*args, **kwargs)

    return wrapper  # type: ignore[return-value]
