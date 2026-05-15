"""RBAC role model for the V2 architecture."""

from .policy import Role, can_transition_workflow

__all__ = ["Role", "can_transition_workflow"]
