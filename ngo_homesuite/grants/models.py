"""Grant domain SQLAlchemy models.

This module is the canonical ownership location for grant-related models.
Core model module imports and re-exports these classes for compatibility.
"""

from ngo_homesuite.models.core import JSON
from ngo_homesuite.models.core import _utcnow_naive
from ngo_homesuite.models.core import db


class Grant(db.Model):
    """Grant opportunity tracked through full lifecycle (prospect -> awarded -> disbursed)."""

    __tablename__ = "grants"

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=True)

    # Funder details
    funder_name = db.Column(db.String(200), nullable=False)
    funder_type = db.Column(db.String(50), default="foundation", nullable=False)  # foundation, government, corporate, other
    funder_contact = db.Column(db.String(200), nullable=True)
    funder_email = db.Column(db.String(120), nullable=True)

    # Grant details
    title = db.Column(db.String(300), nullable=False)
    description = db.Column(db.Text, nullable=True)
    amount_requested = db.Column(db.Float, nullable=True)
    amount_awarded = db.Column(db.Float, nullable=True)
    currency = db.Column(db.String(3), default="USD", nullable=False)

    # Dates
    application_deadline = db.Column(db.Date, nullable=True, index=True)
    submission_date = db.Column(db.Date, nullable=True)
    award_date = db.Column(db.Date, nullable=True)
    start_date = db.Column(db.Date, nullable=True)
    end_date = db.Column(db.Date, nullable=True)
    report_due_date = db.Column(db.Date, nullable=True)

    # Status lifecycle
    status = db.Column(
        db.String(50), default="prospect", nullable=False, index=True
    )  # prospect, in_progress, submitted, awarded, declined, closed, reporting

    # Reporting
    requirements = db.Column(db.Text, nullable=True)  # reporting requirements
    notes = db.Column(db.Text, nullable=True)

    # Timestamps
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    disbursements = db.relationship("GrantDisbursement", backref="grant", cascade="all, delete-orphan")
    budget_lines = db.relationship("GrantBudgetLine", backref="grant", cascade="all, delete-orphan")
    expense_allocations = db.relationship("GrantExpenseAllocation", backref="grant", cascade="all, delete-orphan")
    opportunities = db.relationship("GrantOpportunity", backref="awarded_grant", foreign_keys="GrantOpportunity.awarded_grant_id")
    project = db.relationship("Project", backref="grants")
    organization = db.relationship("Organization", backref="grants")

    def __repr__(self):
        return f"<Grant {self.title} [{self.status}]>"


class GrantDisbursement(db.Model):
    """Individual payment received from a grant award."""

    __tablename__ = "grant_disbursements"

    id = db.Column(db.Integer, primary_key=True)
    grant_id = db.Column(db.Integer, db.ForeignKey("grants.id"), nullable=False, index=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True)
    amount = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(3), default="USD", nullable=False)
    received_date = db.Column(db.Date, nullable=False)
    reference = db.Column(db.String(100), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)

    def __repr__(self):
        return f"<GrantDisbursement grant={self.grant_id} {self.amount}>"


class GrantBudgetLine(db.Model):
    """Line-item budget allocation within a grant award."""

    __tablename__ = "grant_budget_lines"

    id = db.Column(db.Integer, primary_key=True)
    grant_id = db.Column(db.Integer, db.ForeignKey("grants.id"), nullable=False, index=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True)
    category = db.Column(db.String(80), nullable=False, index=True)
    line_name = db.Column(db.String(200), nullable=False)
    allocated_amount = db.Column(db.Float, nullable=False)
    notes = db.Column(db.Text, nullable=True)
    version_id = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    allocations = db.relationship("GrantExpenseAllocation", backref="budget_line", cascade="all, delete-orphan")

    __table_args__ = (
        db.UniqueConstraint("grant_id", "category", name="uq_grant_budget_line_grant_category"),
    )

    __mapper_args__ = {
        "version_id_col": version_id,
    }

    def __repr__(self):
        return f"<GrantBudgetLine grant={self.grant_id} category={self.category}>"


class GrantExpenseAllocation(db.Model):
    """Expense-to-grant line allocation for restricted fund tracking."""

    __tablename__ = "grant_expense_allocations"

    id = db.Column(db.Integer, primary_key=True)
    grant_id = db.Column(db.Integer, db.ForeignKey("grants.id"), nullable=False, index=True)
    budget_line_id = db.Column(db.Integer, db.ForeignKey("grant_budget_lines.id"), nullable=False, index=True)
    expense_id = db.Column(db.Integer, db.ForeignKey("expenses.id"), nullable=False, unique=True, index=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True)
    amount = db.Column(db.Float, nullable=False)
    category = db.Column(db.String(80), nullable=False)
    supporting_document_ref = db.Column(db.String(255), nullable=True)
    version_id = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)

    expense = db.relationship("Expense", backref="grant_allocations")

    __mapper_args__ = {
        "version_id_col": version_id,
    }

    def __repr__(self):
        return f"<GrantExpenseAllocation grant={self.grant_id} expense={self.expense_id} amount={self.amount}>"


class GrantOpportunity(db.Model):
    """Pre-award grant opportunity tracking and forecast metadata."""

    __tablename__ = "grant_opportunities"

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True)
    awarded_grant_id = db.Column(db.Integer, db.ForeignKey("grants.id"), nullable=True, index=True)
    funder_name = db.Column(db.String(200), nullable=False)
    funder_ein = db.Column(db.String(12), nullable=True, index=True)
    program_name = db.Column(db.String(200), nullable=False)
    title = db.Column(db.String(300), nullable=False)
    deadline = db.Column(db.Date, nullable=True, index=True)
    amount_min = db.Column(db.Float, nullable=True)
    amount_max = db.Column(db.Float, nullable=True)
    probability = db.Column(db.Float, nullable=False, default=0.0)
    probability_weighted_amount = db.Column(db.Float, nullable=False, default=0.0)
    status = db.Column(db.String(30), nullable=False, default="identified", index=True)
    notes = db.Column(db.Text, nullable=True)
    version_id = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    proposals = db.relationship("GrantProposal", backref="opportunity", cascade="all, delete-orphan")

    __mapper_args__ = {
        "version_id_col": version_id,
    }

    def __repr__(self):
        return f"<GrantOpportunity {self.title} [{self.status}]>"


class GrantProposal(db.Model):
    """Versioned proposal record linked to a grant opportunity."""

    __tablename__ = "grant_proposals"

    id = db.Column(db.Integer, primary_key=True)
    opportunity_id = db.Column(db.Integer, db.ForeignKey("grant_opportunities.id"), nullable=False, index=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True)
    version_number = db.Column(db.Integer, nullable=False, default=1)
    amount_requested = db.Column(db.Float, nullable=True)
    narrative_summary = db.Column(db.Text, nullable=True)
    submission_date = db.Column(db.Date, nullable=True)
    outcome = db.Column(db.String(30), nullable=False, default="draft", index=True)
    document_ref = db.Column(db.String(255), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    version_id = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    __table_args__ = (
        db.UniqueConstraint("opportunity_id", "version_number", name="uq_grant_proposal_opportunity_version"),
    )

    __mapper_args__ = {
        "version_id_col": version_id,
    }

    def __repr__(self):
        return f"<GrantProposal opp={self.opportunity_id} v{self.version_number} [{self.outcome}]>"


class GrantOutcomeTemplate(db.Model):
    """Outcome metric definition for a grant, optionally tied to a program case type."""

    __tablename__ = "grant_outcome_templates"

    id = db.Column(db.Integer, primary_key=True)
    grant_id = db.Column(db.Integer, db.ForeignKey("grants.id"), nullable=False, index=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True)
    metric_name = db.Column(db.String(120), nullable=False)
    unit = db.Column(db.String(40), nullable=True)
    target_value = db.Column(db.Float, nullable=False)
    baseline_value = db.Column(db.Float, nullable=True)
    program_case_type = db.Column(db.String(50), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    version_id = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    records = db.relationship("GrantOutcomeRecord", backref="template", cascade="all, delete-orphan")

    __table_args__ = (
        db.UniqueConstraint("grant_id", "metric_name", name="uq_grant_outcome_template_grant_metric"),
    )

    __mapper_args__ = {
        "version_id_col": version_id,
    }

    def __repr__(self):
        return f"<GrantOutcomeTemplate grant={self.grant_id} metric={self.metric_name}>"


class GrantOutcomeRecord(db.Model):
    """Recorded progress for a grant outcome metric at a point in time."""

    __tablename__ = "grant_outcome_records"

    id = db.Column(db.Integer, primary_key=True)
    grant_id = db.Column(db.Integer, db.ForeignKey("grants.id"), nullable=False, index=True)
    template_id = db.Column(db.Integer, db.ForeignKey("grant_outcome_templates.id"), nullable=False, index=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True)
    program_case_id = db.Column(db.Integer, db.ForeignKey("program_cases.id"), nullable=True, index=True)
    current_value = db.Column(db.Float, nullable=False)
    recorded_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False, index=True)
    note = db.Column(db.Text, nullable=True)
    source = db.Column(db.String(40), nullable=False, default="manual")
    version_id = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)

    program_case = db.relationship("ProgramCase", backref="grant_outcome_records")

    __mapper_args__ = {
        "version_id_col": version_id,
    }

    def __repr__(self):
        return f"<GrantOutcomeRecord grant={self.grant_id} template={self.template_id} value={self.current_value}>"


class GrantApprovalRequest(db.Model):
    """Approval gate request for sensitive grant actions."""

    __tablename__ = "grant_approval_requests"

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True)
    action_type = db.Column(db.String(60), nullable=False, index=True)
    resource_type = db.Column(db.String(40), nullable=False, index=True)
    resource_id = db.Column(db.Integer, nullable=False, index=True)
    requested_by_user_id = db.Column(db.Integer, nullable=False, index=True)
    requested_by_role = db.Column(db.String(40), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)  # pending, approved, rejected, escalated, executed
    required_approvals = db.Column(db.Integer, nullable=False, default=1)
    approver_roles_json = db.Column(JSON, nullable=True)
    expires_at = db.Column(db.DateTime, nullable=True, index=True)
    escalated_at = db.Column(db.DateTime, nullable=True)
    escalation_role = db.Column(db.String(40), nullable=True)
    payload_json = db.Column(JSON, nullable=True)
    version_id = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    decisions = db.relationship("GrantApprovalDecision", backref="approval_request", cascade="all, delete-orphan")

    __mapper_args__ = {
        "version_id_col": version_id,
    }

    def __repr__(self):
        return f"<GrantApprovalRequest {self.action_type} {self.resource_type}:{self.resource_id} [{self.status}]>"


class GrantApprovalDecision(db.Model):
    """Immutable decision log for grant approval requests."""

    __tablename__ = "grant_approval_decisions"

    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey("grant_approval_requests.id"), nullable=False, index=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True)
    decided_by_user_id = db.Column(db.Integer, nullable=False, index=True)
    decided_by_role = db.Column(db.String(40), nullable=False)
    decision = db.Column(db.String(20), nullable=False)  # approved, rejected
    comment = db.Column(db.Text, nullable=True)
    rationale = db.Column(db.Text, nullable=True)
    decided_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)

    def __repr__(self):
        return f"<GrantApprovalDecision request={self.request_id} decision={self.decision}>"


class GrantApprovalChainConfig(db.Model):
    """Per-organization approval chain policy for grant actions."""

    __tablename__ = "grant_approval_chain_configs"

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True)
    action_type = db.Column(db.String(60), nullable=False, index=True)
    min_amount = db.Column(db.Float, nullable=True)
    max_amount = db.Column(db.Float, nullable=True)
    required_approvals = db.Column(db.Integer, nullable=False, default=1)
    approver_roles_json = db.Column(JSON, nullable=False)
    escalation_role = db.Column(db.String(40), nullable=True)
    sla_hours = db.Column(db.Integer, nullable=False, default=72)
    escalation_sla_hours = db.Column(db.Integer, nullable=False, default=24)
    priority = db.Column(db.Integer, nullable=False, default=100, index=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    created_at = db.Column(db.DateTime, default=_utcnow_naive, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    def __repr__(self):
        return f"<GrantApprovalChainConfig org={self.organization_id} action={self.action_type} priority={self.priority}>"

__all__ = [
    "Grant",
    "GrantDisbursement",
    "GrantBudgetLine",
    "GrantExpenseAllocation",
    "GrantOpportunity",
    "GrantProposal",
    "GrantOutcomeTemplate",
    "GrantOutcomeRecord",
    "GrantApprovalRequest",
    "GrantApprovalDecision",
    "GrantApprovalChainConfig",
]
