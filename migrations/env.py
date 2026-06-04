from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from flask import current_app

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


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def get_metadata():
    return db.metadata


def run_migrations_offline() -> None:
    url = str(current_app.config.get("SQLALCHEMY_DATABASE_URI", ""))
    config.set_main_option("sqlalchemy.url", url)
    context.configure(
        url=url,
        target_metadata=get_metadata(),
        literal_binds=True,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = db.engine
    config.set_main_option("sqlalchemy.url", str(connectable.url))

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=get_metadata(),
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()