from __future__ import annotations

from flask import render_template

from ngo_homesuite.services.opinionated_workflows import (
    run_donation_receipt_followup_workflow,
    run_grant_tracking_reporting_workflow,
    run_program_tracking_impact_workflow,
)


def workflows_page():
    return render_template('workflows.html', active_page='workflows')


def workflow_donation_run(*, donation_id: int, org_id: int, actor: str, db_path: str):
    return run_donation_receipt_followup_workflow(
        donation_id=donation_id,
        actor=actor,
        organization_id=org_id,
        db_path=db_path,
    )


def workflow_grant_run(*, grant_name: str, requested_amount: float, actor: str, db_path: str):
    return run_grant_tracking_reporting_workflow(
        grant_name=grant_name,
        requested_amount=requested_amount,
        actor=actor,
        db_path=db_path,
    )


def workflow_program_run(*, program_name: str, beneficiary_count: int, outcomes: list[dict[str, object]], actor: str, db_path: str):
    return run_program_tracking_impact_workflow(
        program_name=program_name,
        beneficiary_count=beneficiary_count,
        outcomes=outcomes,
        actor=actor,
        db_path=db_path,
    )
