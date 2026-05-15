"""Shared kernel primitives used across the V2 operating system layers."""

from .ids import new_id
from .pii import redact_payload

__all__ = ["new_id", "redact_payload"]
