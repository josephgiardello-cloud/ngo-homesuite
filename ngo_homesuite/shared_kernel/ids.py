from __future__ import annotations

from uuid import uuid4


def new_id(prefix: str) -> str:
    """Generate stable prefixed IDs for workflow and domain entities."""
    return f"{prefix}_{uuid4().hex}"
