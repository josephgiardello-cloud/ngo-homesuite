from __future__ import annotations

from contextvars import ContextVar
from typing import Optional


_write_gate_active: ContextVar[bool] = ContextVar("ngo_homesuite_write_gate_active", default=False)
_write_context_metadata: ContextVar[dict] = ContextVar("ngo_homesuite_write_context_metadata", default={})
_enforcement_mode: ContextVar[str] = ContextVar("ngo_homesuite_enforcement_mode", default="strict")
_bootstrap_mode: ContextVar[bool] = ContextVar("ngo_homesuite_bootstrap_mode", default=False)


class WriteGateViolation(RuntimeError):
    """Raised when a write operation bypasses the WriteGate."""

    def __init__(self, message: str, operation: str | None = None, context: str | None = None):
        self.operation = operation
        self.context = context
        full_msg = f"WriteGate violation: {message}"
        if operation:
            full_msg += f"\n  Operation: {operation}"
        if context:
            full_msg += f"\n  Context: {context}"
        super().__init__(full_msg)


class _CurrentWriteContext:
    """Track write context state and enforce invariants."""

    @property
    def in_write_gate(self) -> bool:
        return bool(_write_gate_active.get())

    @property
    def in_bootstrap(self) -> bool:
        """During bootstrap, enforcement is disabled for app initialization."""
        return bool(_bootstrap_mode.get())

    @property
    def enforcement_mode(self) -> str:
        """'strict' = fail hard on violations, 'warning' = log only."""
        return _enforcement_mode.get()

    @property
    def metadata(self) -> dict:
        """Metadata about current write operation (command_id, actor_id, etc)."""
        return dict(_write_context_metadata.get())

    def get_command_id(self) -> Optional[str]:
        """Get the current command ID if in a write operation."""
        return self.metadata.get("command_id")

    def get_actor_id(self) -> Optional[str]:
        """Get the current actor ID if in a write operation."""
        return self.metadata.get("actor_id")

    def assert_in_write_gate(self, operation: str, context: str | None = None) -> None:
        """Assert that we're inside a WriteGate. Raise if not (unless in bootstrap)."""
        if not self.in_write_gate and not self.in_bootstrap:
            raise WriteGateViolation(
                "All writes must go through WriteGate",
                operation=operation,
                context=context or self.metadata.get("hint", "unknown context"),
            )


current_context = _CurrentWriteContext()


def enter_write_gate(
    *,
    command_id: str | None = None,
    actor_id: str | None = None,
    org_id: str | None = None,
    metadata: dict | None = None,
) -> object:
    """Enter write gate context with optional metadata."""
    meta = dict(metadata or {})
    meta.setdefault("command_id", command_id)
    meta.setdefault("actor_id", actor_id)
    meta.setdefault("org_id", org_id)
    _write_context_metadata.set(meta)
    return _write_gate_active.set(True)


def exit_write_gate(token: object) -> None:
    """Exit write gate context."""
    _write_gate_active.reset(token)
    _write_context_metadata.set({})


def set_enforcement_mode(mode: str) -> object:
    """Set enforcement mode ('strict' or 'warning')."""
    if mode not in ("strict", "warning"):
        raise ValueError(f"Invalid enforcement mode: {mode}")
    return _enforcement_mode.set(mode)


def enter_bootstrap_mode() -> object:
    """Enter bootstrap mode (disables WriteGate enforcement temporarily)."""
    return _bootstrap_mode.set(True)


def exit_bootstrap_mode(token: object) -> None:
    """Exit bootstrap mode."""
    _bootstrap_mode.reset(token)
