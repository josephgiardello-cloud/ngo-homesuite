"""Grant-domain audit helpers."""

from __future__ import annotations


def approval_context(*, action_type: str, resource_type: str, resource_id: int, chain_config_id: int | None = None) -> dict:
    return {
        "action_type": action_type,
        "resource_type": resource_type,
        "resource_id": int(resource_id),
        "chain_config_id": int(chain_config_id) if chain_config_id is not None else None,
    }
