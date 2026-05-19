"""Tests for assessments, referrals, appointments, SMS/Mailchimp integrations,
funder/scheduled reports, and admin UX routes."""
from __future__ import annotations

from datetime import date, datetime, UTC, timezone

import pytest

from ngo_homesuite.models.core import Beneficiary, ExternalCommunicationAuthorization, Organization, ProgramCase, User, db


@pytest.fixture(scope="module")
def app(shared_test_app):
    original_roles_requiring_2fa = shared_test_app.config.get("ROLES_REQUIRING_2FA")
    shared_test_app.config["ROLES_REQUIRING_2FA"] = []
    try:
        yield shared_test_app
    finally:
        shared_test_app.config["ROLES_REQUIRING_2FA"] = original_roles_requiring_2fa


@pytest.fixture()
def client(app):
    return app.test_client()


def _login(client, username: str, password: str) -> None:
    rv = client.post("/auth/login", data={"username": username, "password": password}, follow_redirects=False)
    assert rv.status_code in (302, 303)
    with client.session_transaction() as sess:
        sess["_step_up_verified_at"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def _ensure_user(app, username: str, email: str, role: str, password: str):
    with app.app_context():
        org = Organization.query.filter_by(is_active=True).order_by(Organization.id.asc()).first()
        if org is None:
            org = Organization(name="Test Org New", slug="test-org-new", is_active=True)
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
        else:
            user.email = email
            user.role = role
            user.is_active = True
            if user.organization_id is None:
                user.organization_id = org.id
            user.set_password(password)
        db.session.commit()

        return user.organization_id


def _create_case(app, org_id: int) -> int:
    with app.app_context():
        case = ProgramCase(
            organization_id=org_id,
            title="Test intake case",
            case_type="service",
            status="open",
        )
        db.session.add(case)
        db.session.commit()
        return case.id


# ---------------------------------------------------------------------------
# Assessments
# ---------------------------------------------------------------------------

class TestAssessmentRoutes:
    def test_create_and_list_assessments(self, client, app):
        org_id = _ensure_user(app, "assess_staff", "assess@test.local", "staff", "AssessPass123!")
        _login(client, "assess_staff", "AssessPass123!")
        case_id = _create_case(app, org_id)

        create_rv = client.post(
            f"/programs/cases/{case_id}/assessments",
            json={
                "assessment_date": "2025-01-15",
                "assessment_type": "initial",
                "housing_score": 4.0,
                "food_security_score": 6.0,
                "health_score": 7.0,
                "risk_level": "medium",
                "notes": "Initial intake assessment",
            },
        )
        assert create_rv.status_code == 201
        body = create_rv.get_json()
        assert body["assessment_type"] == "initial"
        assert body["risk_level"] == "medium"
        assert body["total_score"] is not None

        list_rv = client.get(f"/programs/cases/{case_id}/assessments")
        assert list_rv.status_code == 200
        items = list_rv.get_json()
        assert len(items) >= 1
        assert items[0]["housing_score"] == 4.0

    def test_assessment_requires_date(self, client, app):
        org_id = _ensure_user(app, "assess_staff", "assess@test.local", "staff", "AssessPass123!")
        _login(client, "assess_staff", "AssessPass123!")
        case_id = _create_case(app, org_id)

        rv = client.post(f"/programs/cases/{case_id}/assessments", json={"risk_level": "high"})
        assert rv.status_code == 400

    def test_assessment_requires_auth(self, client, app):
        with client.session_transaction() as sess:
            sess.clear()
        rv = client.post("/programs/cases/999/assessments", json={})
        assert rv.status_code in (401, 403, 302)


# ---------------------------------------------------------------------------
# Referrals
# ---------------------------------------------------------------------------

class TestReferralRoutes:
    def test_create_list_update_referral(self, client, app):
        org_id = _ensure_user(app, "referral_staff", "referral@test.local", "staff", "ReferPass123!")
        _login(client, "referral_staff", "ReferPass123!")
        case_id = _create_case(app, org_id)

        create_rv = client.post(
            f"/programs/cases/{case_id}/referrals",
            json={
                "provider_name": "City Housing Authority",
                "referral_date": "2025-02-01",
                "referral_type": "external",
                "service_type": "housing",
                "notes": "Urgent housing need",
            },
        )
        assert create_rv.status_code == 201
        body = create_rv.get_json()
        referral_id = body["id"]
        assert body["provider_name"] == "City Housing Authority"
        assert body["status"] == "pending"

        list_rv = client.get(f"/programs/cases/{case_id}/referrals")
        assert list_rv.status_code == 200
        assert len(list_rv.get_json()) >= 1

        update_rv = client.patch(
            f"/programs/cases/{case_id}/referrals/{referral_id}",
            json={"status": "accepted", "outcome_notes": "Client accepted"},
        )
        assert update_rv.status_code == 200
        assert update_rv.get_json()["status"] == "accepted"

    def test_referral_requires_provider_name(self, client, app):
        org_id = _ensure_user(app, "referral_staff", "referral@test.local", "staff", "ReferPass123!")
        _login(client, "referral_staff", "ReferPass123!")
        case_id = _create_case(app, org_id)

        rv = client.post(f"/programs/cases/{case_id}/referrals", json={"referral_date": "2025-01-01"})
        assert rv.status_code == 400


# ---------------------------------------------------------------------------
# Appointments
# ---------------------------------------------------------------------------

class TestAppointmentRoutes:
    def test_appointment_full_lifecycle(self, client, app):
        org_id = _ensure_user(app, "appt_staff", "appt@test.local", "staff", "ApptPass123!")
        _login(client, "appt_staff", "ApptPass123!")
        case_id = _create_case(app, org_id)

        # Create
        create_rv = client.post(
            "/programs/appointments",
            json={
                "title": "Intake interview",
                "scheduled_at": "2025-03-10T10:00:00",
                "appointment_type": "intake",
                "case_id": case_id,
                "duration_minutes": 45,
                "location": "Office 3B",
            },
        )
        assert create_rv.status_code == 201
        body = create_rv.get_json()
        appt_id = body["id"]
        assert body["title"] == "Intake interview"
        assert body["status"] == "scheduled"

        # List
        list_rv = client.get(f"/programs/appointments?case_id={case_id}")
        assert list_rv.status_code == 200
        items = list_rv.get_json()
        assert any(a["id"] == appt_id for a in items)

        # Get
        get_rv = client.get(f"/programs/appointments/{appt_id}")
        assert get_rv.status_code == 200
        assert get_rv.get_json()["location"] == "Office 3B"

        # Update status
        update_rv = client.patch(f"/programs/appointments/{appt_id}", json={"status": "confirmed"})
        assert update_rv.status_code == 200
        assert update_rv.get_json()["status"] == "confirmed"

        # Cancel
        cancel_rv = client.delete(f"/programs/appointments/{appt_id}")
        assert cancel_rv.status_code == 204

        # Verify cancelled
        get_rv2 = client.get(f"/programs/appointments/{appt_id}")
        assert get_rv2.get_json()["status"] == "cancelled"

    def test_appointment_requires_title(self, client, app):
        org_id = _ensure_user(app, "appt_staff", "appt@test.local", "staff", "ApptPass123!")
        _login(client, "appt_staff", "ApptPass123!")

        rv = client.post("/programs/appointments", json={"scheduled_at": "2025-03-10T10:00:00"})
        assert rv.status_code == 400

    def test_appointment_requires_scheduled_at(self, client, app):
        org_id = _ensure_user(app, "appt_staff", "appt@test.local", "staff", "ApptPass123!")
        _login(client, "appt_staff", "ApptPass123!")

        rv = client.post("/programs/appointments", json={"title": "Meeting"})
        assert rv.status_code == 400


# ---------------------------------------------------------------------------
# SMS routes
# ---------------------------------------------------------------------------

class TestSMSRoutes:
    def test_sms_notify_sends_stub(self, client, app):
        org_id = _ensure_user(app, "sms_admin", "sms@test.local", "admin", "SmsPass123!")
        _login(client, "sms_admin", "SmsPass123!")

        # No Twilio credentials in test env → expect success=False gracefully
        rv = client.post(
            "/integrations/sms/notify",
            json={"to": "+15551234567", "body": "Test reminder"},
        )
        assert rv.status_code == 200
        body = rv.get_json()
        assert "success" in body

    def test_sms_notify_requires_to(self, client, app):
        _ensure_user(app, "sms_admin", "sms@test.local", "admin", "SmsPass123!")
        _login(client, "sms_admin", "SmsPass123!")

        rv = client.post("/integrations/sms/notify", json={"body": "hello"})
        assert rv.status_code == 400

    def test_sms_notify_e164_validation(self, client, app):
        _ensure_user(app, "sms_admin", "sms@test.local", "admin", "SmsPass123!")
        _login(client, "sms_admin", "SmsPass123!")

        rv = client.post("/integrations/sms/notify", json={"to": "5551234567", "body": "hello"})
        assert rv.status_code == 400

    def test_sms_notify_requires_auth(self, client, app):
        with client.session_transaction() as sess:
            sess.clear()
        rv = client.post("/integrations/sms/notify", json={"to": "+15551234567", "body": "x"})
        assert rv.status_code in (401, 403, 302)


# ---------------------------------------------------------------------------
# Mailchimp sync routes
# ---------------------------------------------------------------------------

class TestMailchimpRoutes:
    def test_mailchimp_sync_no_credentials(self, client, app):
        """Sync should return result dict even without MAILCHIMP_API_KEY."""
        org_id = _ensure_user(app, "mc_admin", "mc@test.local", "admin", "McPass123!")
        _login(client, "mc_admin", "McPass123!")

        rv = client.post("/integrations/mailchimp/sync", json={})
        assert rv.status_code == 200
        body = rv.get_json()
        assert "synced" in body
        assert "failed" in body

    def test_mailchimp_subscribe_requires_email(self, client, app):
        _ensure_user(app, "mc_admin", "mc@test.local", "admin", "McPass123!")
        _login(client, "mc_admin", "McPass123!")

        rv = client.post("/integrations/mailchimp/subscribe", json={"first_name": "Alice"})
        assert rv.status_code == 400


# ---------------------------------------------------------------------------
# Funder reports
# ---------------------------------------------------------------------------

class TestFunderReport:
    def test_funder_report_returns_structure(self, client, app):
        org_id = _ensure_user(app, "report_staff", "report@test.local", "staff", "ReportPass123!")
        _login(client, "report_staff", "ReportPass123!")

        rv = client.get("/reports/funder?funder_name=Ford+Foundation&start=2024-01-01&end=2024-12-31")
        assert rv.status_code == 200
        body = rv.get_json()
        assert body["funder_name"] == "Ford Foundation"
        assert "donations" in body
        assert "grants" in body
        assert "summary" in body

    def test_funder_report_requires_name(self, client, app):
        _ensure_user(app, "report_staff", "report@test.local", "staff", "ReportPass123!")
        _login(client, "report_staff", "ReportPass123!")

        rv = client.get("/reports/funder")
        assert rv.status_code == 400

    def test_giving_trends(self, client, app):
        _ensure_user(app, "report_staff", "report@test.local", "staff", "ReportPass123!")
        _login(client, "report_staff", "ReportPass123!")

        rv = client.get("/reports/trends/giving")
        assert rv.status_code == 200
        assert isinstance(rv.get_json(), list)

    def test_retention_trends(self, client, app):
        _ensure_user(app, "report_staff", "report@test.local", "staff", "ReportPass123!")
        _login(client, "report_staff", "ReportPass123!")

        rv = client.get("/reports/trends/retention")
        assert rv.status_code == 200


# ---------------------------------------------------------------------------
# Scheduled reports
# ---------------------------------------------------------------------------

class TestScheduledReports:
    def test_scheduled_report_full_lifecycle(self, client, app):
        org_id = _ensure_user(app, "sched_admin", "sched@test.local", "admin", "SchedPass123!")
        _login(client, "sched_admin", "SchedPass123!")

        # Create
        create_rv = client.post(
            "/reports/scheduled",
            json={
                "name": "Monthly Impact Report",
                "report_type": "impact",
                "frequency": "monthly",
                "delivery_email": "director@ngo.org",
                "parameters": {"currency": "USD"},
            },
        )
        assert create_rv.status_code == 201
        body = create_rv.get_json()
        report_id = body["id"]
        assert body["report_type"] == "impact"
        assert body["next_run_at"] is not None

        # List
        list_rv = client.get("/reports/scheduled")
        assert list_rv.status_code == 200
        items = list_rv.get_json()
        assert any(r["id"] == report_id for r in items)

        # Update
        update_rv = client.patch(f"/reports/scheduled/{report_id}", json={"is_active": False})
        assert update_rv.status_code == 200
        assert update_rv.get_json()["is_active"] is False

        # Delete
        del_rv = client.delete(f"/reports/scheduled/{report_id}")
        assert del_rv.status_code == 204

        # Confirm gone
        list_rv2 = client.get("/reports/scheduled")
        assert all(r["id"] != report_id for r in list_rv2.get_json())

    def test_scheduled_report_requires_name(self, client, app):
        _ensure_user(app, "sched_admin", "sched@test.local", "admin", "SchedPass123!")
        _login(client, "sched_admin", "SchedPass123!")

        rv = client.post("/reports/scheduled", json={"report_type": "impact", "frequency": "monthly"})
        assert rv.status_code == 400


# ---------------------------------------------------------------------------
# Admin UX
# ---------------------------------------------------------------------------

class TestAdminRoutes:
    def test_list_users_requires_admin(self, client, app):
        org_id = _ensure_user(app, "viewer_user", "viewer@test.local", "viewer", "ViewerPass123!")
        _login(client, "viewer_user", "ViewerPass123!")

        rv = client.get("/admin/users")
        assert rv.status_code == 403

    def test_admin_can_list_users(self, client, app):
        org_id = _ensure_user(app, "admin_user2", "admin2@test.local", "admin", "AdminPass123!")
        _login(client, "admin_user2", "AdminPass123!")

        rv = client.get("/admin/users")
        assert rv.status_code == 200
        users = rv.get_json()
        assert isinstance(users, list)
        assert any(u["username"] == "admin_user2" for u in users)

    def test_admin_update_role(self, client, app):
        org_id = _ensure_user(app, "admin_user2", "admin2@test.local", "admin", "AdminPass123!")
        # Create a target user in same org
        with app.app_context():
            target = User.query.filter_by(username="role_target").first()
            if target is None:
                target = User(
                    username="role_target",
                    email="role_target@test.local",
                    role="viewer",
                    is_active=True,
                    organization_id=org_id,
                )
                target.set_password("TargetPass123!")
                db.session.add(target)
                db.session.commit()
            target_id = target.id

        _login(client, "admin_user2", "AdminPass123!")
        rv = client.patch(f"/admin/users/{target_id}/role", json={"role": "staff"})
        assert rv.status_code == 200
        assert rv.get_json()["role"] == "staff"

    def test_admin_cannot_change_own_role(self, client, app):
        org_id = _ensure_user(app, "admin_user2", "admin2@test.local", "admin", "AdminPass123!")
        _login(client, "admin_user2", "AdminPass123!")

        with app.app_context():
            self_user = User.query.filter_by(username="admin_user2").first()
            self_id = self_user.id

        rv = client.patch(f"/admin/users/{self_id}/role", json={"role": "viewer"})
        assert rv.status_code == 400

    def test_admin_invalid_role_rejected(self, client, app):
        org_id = _ensure_user(app, "admin_user2", "admin2@test.local", "admin", "AdminPass123!")
        _login(client, "admin_user2", "AdminPass123!")

        with app.app_context():
            target = User.query.filter_by(username="role_target").first()
            target_id = target.id if target else 999

        rv = client.patch(f"/admin/users/{target_id}/role", json={"role": "superuser"})
        assert rv.status_code == 400

    def test_admin_can_update_external_comms_permission(self, client, app):
        org_id = _ensure_user(app, "admin_user2", "admin2@test.local", "admin", "AdminPass123!")
        with app.app_context():
            target = User.query.filter_by(username="perm_target").first()
            if target is None:
                target = User(
                    username="perm_target",
                    email="perm_target@test.local",
                    role="staff",
                    is_active=True,
                    organization_id=org_id,
                )
                target.set_password("PermTarget123!")
                db.session.add(target)
                db.session.commit()
            target_id = target.id

        _login(client, "admin_user2", "AdminPass123!")
        rv = client.patch(
            f"/admin/users/{target_id}/permissions",
            json={"can_authorize_external_comms": True},
        )
        assert rv.status_code == 200
        payload = rv.get_json()
        assert payload["can_authorize_external_comms"] is True
        assert payload["effective_external_comms_authority"] is True

    def test_admin_update_external_comms_permission_requires_flag(self, client, app):
        _ensure_user(app, "admin_user2", "admin2@test.local", "admin", "AdminPass123!")
        _login(client, "admin_user2", "AdminPass123!")

        with app.app_context():
            target = User.query.filter_by(username="perm_target").first()
            target_id = target.id if target else 999999

        rv = client.patch(f"/admin/users/{target_id}/permissions", json={})
        assert rv.status_code == 400

    def test_admin_can_list_external_comms_audit(self, client, app):
        org_id = _ensure_user(app, "admin_user2", "admin2@test.local", "admin", "AdminPass123!")
        with app.app_context():
            admin_user = User.query.filter_by(username="admin_user2").first()
            assert admin_user is not None
            other_org = Organization.query.filter_by(slug="audit-other-org").first()
            if other_org is None:
                other_org = Organization(name="Audit Other Org", slug="audit-other-org", is_active=True)
                db.session.add(other_org)
                db.session.commit()

            foreign_user = User.query.filter_by(username="foreign_admin").first()
            if foreign_user is None:
                foreign_user = User(
                    username="foreign_admin",
                    email="foreign_admin@test.local",
                    role="admin",
                    is_active=True,
                    organization_id=other_org.id,
                )
                foreign_user.set_password("ForeignAdmin123!")
                db.session.add(foreign_user)
                db.session.commit()

            owned = ExternalCommunicationAuthorization(
                organization_id=org_id,
                user_id=admin_user.id,
                username="admin_user2",
                user_role="admin",
                channel="email",
                communication_type="campaign_bulk_email",
                campaign_id=101,
                batch_id=202,
                warning_acknowledged=True,
                confirmation_phrase="I CONFIRM HUMAN REVIEW",
                reviewer_name="Compliance Lead",
                reviewer_role="Director",
                details_json={"reason": "test"},
            )
            foreign = ExternalCommunicationAuthorization(
                organization_id=other_org.id,
                user_id=foreign_user.id,
                username="foreign_admin",
                user_role="admin",
                channel="email",
                communication_type="campaign_bulk_email",
                warning_acknowledged=True,
                confirmation_phrase="I CONFIRM HUMAN REVIEW",
                reviewer_name="Other Org Reviewer",
            )
            db.session.add(owned)
            db.session.add(foreign)
            db.session.commit()

        _login(client, "admin_user2", "AdminPass123!")

        rv = client.get("/admin/external-comms/audit?channel=email&reviewer_name=Compliance")
        assert rv.status_code == 200
        payload = rv.get_json()
        assert payload["count"] >= 1
        usernames = {item["username"] for item in payload["items"]}
        assert "admin_user2" in usernames
        assert "foreign_admin" not in usernames

    def test_admin_external_comms_audit_rejects_invalid_datetime(self, client, app):
        _ensure_user(app, "admin_user2", "admin2@test.local", "admin", "AdminPass123!")
        _login(client, "admin_user2", "AdminPass123!")

        rv = client.get("/admin/external-comms/audit?authorized_from=not-a-date")
        assert rv.status_code == 400
        assert "authorized_from" in rv.get_json()["error"]

    def test_admin_custom_fields_schema_round_trip(self, client, app):
        _ensure_user(app, "admin_user2", "admin2@test.local", "admin", "AdminPass123!")
        _login(client, "admin_user2", "AdminPass123!")

        get_rv = client.get("/admin/custom-fields/schema")
        assert get_rv.status_code == 200
        assert "schema" in get_rv.get_json()

        put_rv = client.put(
            "/admin/custom-fields/schema",
            json={
                "schema": {
                    "donor": [
                        {
                            "key": "preferred_language",
                            "label": "Preferred Language",
                            "type": "select",
                            "required": False,
                            "options": ["English", "Spanish"],
                        }
                    ],
                    "campaign": [
                        {
                            "key": "priority_band",
                            "label": "Priority Band",
                            "type": "text",
                            "required": False,
                            "options": [],
                        }
                    ],
                }
            },
        )
        assert put_rv.status_code == 200
        schema = put_rv.get_json()["schema"]
        assert len(schema["donor"]) == 1
        assert schema["donor"][0]["key"] == "preferred_language"

        get_again_rv = client.get("/admin/custom-fields/schema")
        assert get_again_rv.status_code == 200
        stored = get_again_rv.get_json()["schema"]
        assert stored["campaign"][0]["key"] == "priority_band"

    def test_admin_custom_fields_schema_rejects_invalid_payload(self, client, app):
        _ensure_user(app, "admin_user2", "admin2@test.local", "admin", "AdminPass123!")
        _login(client, "admin_user2", "AdminPass123!")

        rv = client.put(
            "/admin/custom-fields/schema",
            json={
                "schema": {
                    "donor": [
                        {
                            "key": "bad-key-with-dash",
                            "label": "Bad Key",
                            "type": "text",
                            "required": False,
                            "options": [],
                        }
                    ],
                }
            },
        )
        assert rv.status_code == 400
        assert "key must match pattern" in rv.get_json()["error"]

    def test_admin_get_org(self, client, app):
        _ensure_user(app, "admin_user2", "admin2@test.local", "admin", "AdminPass123!")
        _login(client, "admin_user2", "AdminPass123!")

        rv = client.get("/admin/org")
        assert rv.status_code == 200
        assert "name" in rv.get_json()

    def test_admin_list_roles(self, client, app):
        _ensure_user(app, "admin_user2", "admin2@test.local", "admin", "AdminPass123!")
        _login(client, "admin_user2", "AdminPass123!")

        rv = client.get("/admin/roles")
        assert rv.status_code == 200
        body = rv.get_json()
        assert "roles" in body
        role_names = [r["role"] for r in body["roles"]]
        assert "admin" in role_names
        assert "staff" in role_names

    def test_remove_user_from_org(self, client, app):
        org_id = _ensure_user(app, "admin_user2", "admin2@test.local", "admin", "AdminPass123!")
        with app.app_context():
            removable = User.query.filter_by(username="removable_user").first()
            if removable is None:
                removable = User(
                    username="removable_user",
                    email="removable@test.local",
                    role="viewer",
                    is_active=True,
                    organization_id=org_id,
                )
                removable.set_password("RemovablePass123!")
                db.session.add(removable)
                db.session.commit()
            removable_id = removable.id

        _login(client, "admin_user2", "AdminPass123!")
        rv = client.delete(f"/admin/users/{removable_id}")
        assert rv.status_code == 204

        # Verify removed from org
        with app.app_context():
            u = db.session.get(User, removable_id)
            assert u.organization_id is None
            assert u.is_active is False
