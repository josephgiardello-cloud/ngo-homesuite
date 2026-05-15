from __future__ import annotations

from datetime import datetime, UTC

import pytest

from ngo_homesuite.models.core import Beneficiary, Organization, ProgramCase, User, db


@pytest.fixture(scope="module")
def app(shared_test_app):
    return shared_test_app


@pytest.fixture()
def client(app):
    return app.test_client()



def _login(client, username: str, password: str) -> None:
    client.post("/auth/login", data={"username": username, "password": password})



def _ensure_user(app, username: str, email: str, role: str, password: str):
    with app.app_context():
        org = Organization.query.filter_by(is_active=True).order_by(Organization.id.asc()).first()
        if org is None:
            org = Organization(name="Program Org", slug="program-org", is_active=True)
            db.session.add(org)
            db.session.commit()

        user = User.query.filter_by(username=username).first()
        if user is None:
            user = User(
                username=username,
                email=email,
                role=role,
                is_active=True,
                organization_id=org.id,
            )
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
        elif user.organization_id is None:
            user.organization_id = org.id
            db.session.commit()

        return user.organization_id



def test_program_case_management_routes(client, app):
    org_id = _ensure_user(app, "program_staff", "program_staff@test.local", "staff", "program_staff_pass_123")
    _login(client, "program_staff", "program_staff_pass_123")

    with app.app_context():
        beneficiary = Beneficiary(
            organization_id=org_id,
            first_name="Lucia",
            last_name="Mendes",
            status="active",
            program="Education",
        )
        db.session.add(beneficiary)
        db.session.commit()
        beneficiary_id = beneficiary.id

    create_rv = client.post(
        "/programs/cases",
        json={
            "title": "Student retention case",
            "beneficiary_id": beneficiary_id,
            "case_type": "service",
            "outcome_metric": "attendance_rate",
            "target_outcome_value": 90,
            "intake_stage": "assessment",
        },
    )
    assert create_rv.status_code == 201
    case_id = create_rv.get_json()["id"]

    service_rv = client.post(
        f"/programs/cases/{case_id}/service-logs",
        json={
            "service_type": "mentoring",
            "service_date": datetime.now(UTC).isoformat(),
            "duration_minutes": 45,
            "outcome_note": "Weekly mentoring completed",
        },
    )
    assert service_rv.status_code == 201

    outcome_rv = client.post(
        f"/programs/cases/{case_id}/outcomes",
        json={
            "metric_name": "attendance_rate",
            "current_value": 72,
            "target_value": 90,
            "note": "Month 1",
        },
    )
    assert outcome_rv.status_code == 201

    logs_rv = client.get(f"/programs/cases/{case_id}/service-logs")
    assert logs_rv.status_code == 200
    logs_payload = logs_rv.get_json()
    assert len(logs_payload) == 1
    assert logs_payload[0]["service_type"] == "mentoring"

    progress_rv = client.get(f"/programs/cases/{case_id}/progress")
    assert progress_rv.status_code == 200
    progress_payload = progress_rv.get_json()
    assert progress_payload["service_count"] == 1
    assert progress_payload["progress_percent"] == 80.0
    assert len(progress_payload["metrics"]) == 1
    assert len(progress_payload["timeline"]) >= 2

    intake_rv = client.put(
        f"/programs/intake/beneficiaries/{beneficiary_id}",
        json={
            "phone": "+1-555-1234",
            "city": "Austin",
            "notes": "Mobile intake complete",
        },
    )
    assert intake_rv.status_code == 200
    intake_payload = intake_rv.get_json()
    assert intake_payload["id"] == beneficiary_id



def test_program_route_input_validation(client, app):
    _ensure_user(app, "program_admin", "program_admin@test.local", "admin", "program_admin_pass_123")
    _login(client, "program_admin", "program_admin_pass_123")

    rv_create = client.post("/programs/cases", json={})
    assert rv_create.status_code == 400

    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        case = ProgramCase(
            organization_id=org.id,
            title="Validation case",
            case_type="service",
            status="open",
        )
        db.session.add(case)
        db.session.commit()
        case_id = case.id

    rv_service = client.post(f"/programs/cases/{case_id}/service-logs", json={})
    assert rv_service.status_code == 400

    rv_outcome_1 = client.post(f"/programs/cases/{case_id}/outcomes", json={"current_value": 4})
    assert rv_outcome_1.status_code == 400

    rv_outcome_2 = client.post(f"/programs/cases/{case_id}/outcomes", json={"metric_name": "attendance"})
    assert rv_outcome_2.status_code == 400
