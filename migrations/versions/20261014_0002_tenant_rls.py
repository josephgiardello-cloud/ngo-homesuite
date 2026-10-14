"""Force PostgreSQL tenant policies on the reviewed direct/indirect table set."""
from __future__ import annotations

from alembic import op

revision = "20261014_0002"
down_revision = "20260604_0001"
branch_labels = None
depends_on = None

# Freeze this revision's table contract; future models need a new migration.
DIRECT_TABLES = (
    "ai_conversations", "accounting_sync_logs", "beneficiaries",
    "beneficiary_appointments", "beneficiary_assessments", "beneficiary_referrals",
    "beneficiary_service_logs", "bootstrap_tokens", "campaigns",
    "campaign_communication_preferences", "campaign_email_batches",
    "campaign_email_deliveries", "campaign_email_opt_outs", "case_activities",
    "case_outcome_metrics", "collaboration_channels", "collaboration_channel_members",
    "collaboration_messages", "collaboration_presence", "donations", "donors",
    "donor_engagement_scores", "donor_journey_automation_events", "donor_soft_credits",
    "expenses", "external_communication_authorizations", "form_submission_events",
    "funds", "grants", "grant_approval_chain_configs", "grant_approval_decisions",
    "grant_approval_requests", "grant_budget_lines", "grant_budget_transactions",
    "grant_disbursements", "grant_expense_allocations", "grant_opportunities",
    "grant_outcome_records", "grant_outcome_templates", "grant_proposals", "grant_scores",
    "grant_search_alerts", "grant_search_profiles", "membership_records", "membership_tiers",
    "p2p_pages", "program_cases", "program_case_documents", "program_case_followups",
    "program_case_goals", "program_case_tasks", "projects", "project_milestones",
    "recurring_donation_plans", "scheduled_reports", "smart_groups",
    "stewardship_enrollments", "stewardship_journeys", "tasks", "task_dependencies",
    "task_reminders", "training_courses", "volunteers", "volunteer_shifts",
    "volunteer_trainings",
)
STRING_ORG_TABLES = ("workflow_events_v2", "workflow_instances_v2")
INDIRECT_TABLES = {
    "ai_messages": (("conversation_id", "ai_conversations"),),
    "donation_receipts": (("donation_id", "donations"),),
    "stewardship_steps": (("journey_id", "stewardship_journeys"),),
    "p2p_page_donations": (("page_id", "p2p_pages"), ("donation_id", "donations")),
}
TENANT_SETTING = "NULLIF(current_setting('ngo.organization_id', true), '')"


def _policies():
    for table in DIRECT_TABLES:
        yield table, f'"organization_id"::text = {TENANT_SETTING}'
    for table in STRING_ORG_TABLES:
        yield table, f'"org_id" = {TENANT_SETTING}'
    for table, parents in INDIRECT_TABLES.items():
        yield table, " AND ".join(
            f'EXISTS (SELECT 1 FROM "{parent}" AS owner WHERE '
            f'owner.id = "{table}"."{key}" AND owner.organization_id::text = {TENANT_SETTING})'
            for key, parent in parents
        )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table, predicate in _policies():
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'CREATE POLICY ngo_tenant_isolation ON "{table}" '
            f'USING ({predicate}) WITH CHECK ({predicate})'
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table, _ in _policies():
        op.execute(f'DROP POLICY ngo_tenant_isolation ON "{table}"')
        op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
