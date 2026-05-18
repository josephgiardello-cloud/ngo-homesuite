"""Persistence layer with event-first repositories and projections."""

from .interfaces import UnitOfWorkPort, WorkflowDefinitionRepositoryPort, WorkflowRepositoryPort
from .uow import SqlAlchemyUnitOfWork
from .write_context import (
	current_context,
	enter_write_gate,
	exit_write_gate,
	set_enforcement_mode,
	enter_bootstrap_mode,
	exit_bootstrap_mode,
	WriteGateViolation,
)
from .base_repository import BaseRepository, enforce_write_gate

__all__ = [
	"UnitOfWorkPort",
	"WorkflowRepositoryPort",
	"WorkflowDefinitionRepositoryPort",
	"SqlAlchemyUnitOfWork",
	"current_context",
	"enter_write_gate",
	"exit_write_gate",
	"set_enforcement_mode",
	"enter_bootstrap_mode",
	"exit_bootstrap_mode",
	"WriteGateViolation",
	"BaseRepository",
	"enforce_write_gate",
]
