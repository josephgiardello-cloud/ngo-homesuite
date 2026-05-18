from __future__ import annotations

from contextvars import ContextVar

_write_gate_active: ContextVar[bool] = ContextVar("ngo_homesuite_write_gate_active", default=False)


class _CurrentWriteContext:
    @property
    def in_write_gate(self) -> bool:
        return bool(_write_gate_active.get())


current_context = _CurrentWriteContext()


def enter_write_gate() -> object:
    return _write_gate_active.set(True)


def exit_write_gate(token: object) -> None:
    _write_gate_active.reset(token)
