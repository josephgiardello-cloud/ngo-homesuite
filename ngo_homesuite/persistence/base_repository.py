"""Base repository class with global WriteGate enforcement."""

from __future__ import annotations

from typing import Any, Callable, TypeVar

from ngo_homesuite.persistence.write_context import WriteGateViolation, current_context

T = TypeVar("T")


class BaseRepository:
    """All repositories must inherit from this to enforce WriteGate invariants.

    INVARIANT: Any mutation (create, save, update, delete) must happen inside WriteGate.
    This is NOT optional—it's a hard guard.
    """

    _write_methods = {"save", "create", "update", "delete", "delete_all", "append", "append_batch"}

    def __getattribute__(self, name: str) -> Any:
        """Intercept method calls to enforce WriteGate on write methods."""
        obj = super().__getattribute__(name)

        # Only apply enforcement to actual methods
        if not callable(obj) or name.startswith("_"):
            return obj

        # Check if this is a write method
        write_methods = super().__getattribute__("_write_methods")
        if name not in write_methods:
            return obj

        # Wrap the method to enforce WriteGate
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            current_context.assert_in_write_gate(
                operation=f"{self.__class__.__name__}.{name}",
                context=f"Repository mutation on {self.__class__.__name__}",
            )
            return obj(*args, **kwargs)

        return wrapped

    @classmethod
    def register_write_method(cls, method_name: str) -> None:
        """Register a custom method as a write method (will enforce WriteGate)."""
        if not hasattr(cls, "_write_methods"):
            cls._write_methods = set(cls._write_methods or {})
        cls._write_methods.add(method_name)


def enforce_write_gate(func: Callable) -> Callable:
    """Decorator to enforce WriteGate on a specific method.

    Use this for custom write methods that don't follow naming conventions.
    """

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        current_context.assert_in_write_gate(
            operation=f"{func.__module__}.{func.__qualname__}",
            context="Decorated write operation",
        )
        return func(*args, **kwargs)

    wrapper.__name__ = func.__name__
    wrapper.__doc__ = func.__doc__
    return wrapper
