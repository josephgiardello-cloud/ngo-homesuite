"""Alembic baseline for non-SQLite production deployments.

Revision ID: 20260604_0001
Revises:
Create Date: 2026-06-04 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

from ngo_homesuite.audit.security_events import SecurityAuditEvent  # noqa: F401
from ngo_homesuite.auth.bootstrap import BootstrapToken  # noqa: F401
from ngo_homesuite.grants.models import (  # noqa: F401
    Grant,
    GrantApprovalChainConfig,
    GrantApprovalDecision,
    GrantApprovalRequest,
    GrantBudgetLine,
    GrantBudgetTransaction,
    GrantDisbursement,
    GrantExpenseAllocation,
    GrantOpportunity,
    GrantOutcomeRecord,
    GrantOutcomeTemplate,
    GrantProposal,
    GrantScore,
    GrantSearchAlert,
    GrantSearchProfile,
)
from ngo_homesuite.models.core import db
from ngo_homesuite.persistence.models.workflow_tables import (  # noqa: F401
    WorkflowDefinitionRecord,
    WorkflowEventRecord,
    WorkflowInstanceRecord,
)


revision = "20260604_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    db.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    db.metadata.drop_all(bind=bind)