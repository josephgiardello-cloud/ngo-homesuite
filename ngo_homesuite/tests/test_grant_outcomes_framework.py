from __future__ import annotations

import pytest

from ngo_homesuite.models.core import Organization, ProgramCase, db
from ngo_homesuite.services import grant_outcomes_service, grant_service


@pytest.fixture(scope="module")
def app(shared_test_app):
    return shared_test_app


@pytest.fixture()
def ctx(app):
    with app.app_context():
        yield


def _mk_org(name: str, slug: str) -> Organization:
    org = Organization(name=name, slug=slug, is_active=True)
    db.session.add(org)
    db.session.flush()
    return org


def _mk_awarded_grant(org_id: int):
    grant = grant_service.create_grant(
        organization_id=org_id,
        funder_name="Outcome Funder",
        title="Outcome Grant",
        amount_requested=10000,
    )
    grant_service.advance_grant_status(grant.id, org_id, new_status="submitted")
    grant_service.advance_grant_status(grant.id, org_id, new_status="awarded", amount_awarded=10000)
    return grant


def test_define_outcome_template_and_record_metric(ctx):
    org = _mk_org("Outcome Org A", "outcome-org-a")
    grant = _mk_awarded_grant(org.id)

    template = grant_outcomes_service.define_outcome_template(
        grant.id,
        org.id,
        metric_name="Families Housed",
        target_value=40,
        baseline_value=5,
        unit="families",
    )

    recorded = grant_outcomes_service.record_outcome(
        grant.id,
        org.id,
        template_id=template.id,
        current_value=18,
        note="Q1 update",
    )

    assert recorded.id is not None
    summary = grant_outcomes_service.outcome_summary(grant.id, org.id)
    assert summary["metric_count"] == 1
    assert summary["metrics"][0]["variance"] == -22.0


def test_outcome_template_rejects_duplicate_metric(ctx):
    org = _mk_org("Outcome Org B", "outcome-org-b")
    grant = _mk_awarded_grant(org.id)

    grant_outcomes_service.define_outcome_template(
        grant.id,
        org.id,
        metric_name="Clients Served",
        target_value=100,
    )

    with pytest.raises(ValueError, match="already exists"):
        grant_outcomes_service.define_outcome_template(
            grant.id,
            org.id,
            metric_name="Clients Served",
            target_value=120,
        )


def test_record_outcome_requires_case_link_if_program_case_id_provided(ctx):
    org = _mk_org("Outcome Org C", "outcome-org-c")
    grant = _mk_awarded_grant(org.id)
    template = grant_outcomes_service.define_outcome_template(
        grant.id,
        org.id,
        metric_name="Job Placements",
        target_value=25,
    )

    with pytest.raises(ValueError, match="must reference a case"):
        grant_outcomes_service.record_outcome(
            grant.id,
            org.id,
            template_id=template.id,
            current_value=10,
            program_case_id=999999,
        )


def test_record_outcome_allows_valid_grant_linked_program_case(ctx):
    org = _mk_org("Outcome Org D", "outcome-org-d")
    grant = _mk_awarded_grant(org.id)
    template = grant_outcomes_service.define_outcome_template(
        grant.id,
        org.id,
        metric_name="Workforce Certifications",
        target_value=30,
        program_case_type="grant_deliverable",
    )

    program_case = ProgramCase(
        organization_id=org.id,
        grant_id=grant.id,
        title="Grant Deliverable Case",
        case_type="grant_deliverable",
        status="in_progress",
    )
    db.session.add(program_case)
    db.session.flush()

    rec = grant_outcomes_service.record_outcome(
        grant.id,
        org.id,
        template_id=template.id,
        current_value=12,
        program_case_id=program_case.id,
        source="service_log",
    )

    assert int(rec.program_case_id or 0) == int(program_case.id)


def test_grant_variance_report_rolls_up_metric_progress(ctx):
    org = _mk_org("Outcome Org E", "outcome-org-e")
    grant = _mk_awarded_grant(org.id)

    t1 = grant_outcomes_service.define_outcome_template(
        grant.id,
        org.id,
        metric_name="Households Supported",
        target_value=80,
    )
    t2 = grant_outcomes_service.define_outcome_template(
        grant.id,
        org.id,
        metric_name="Training Sessions",
        target_value=20,
    )

    grant_outcomes_service.record_outcome(grant.id, org.id, template_id=t1.id, current_value=60)
    grant_outcomes_service.record_outcome(grant.id, org.id, template_id=t2.id, current_value=18)

    report = grant_outcomes_service.grant_variance_report(org.id)
    assert report["grant_count"] >= 1
    assert any(int(item["grant_id"]) == int(grant.id) for item in report["grants"])
