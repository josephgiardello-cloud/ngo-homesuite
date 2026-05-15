"""Repository interfaces enforcing API -> Service -> Domain -> Repository boundaries."""

from .workflow_repository import WorkflowRepository

__all__ = ["WorkflowRepository"]
