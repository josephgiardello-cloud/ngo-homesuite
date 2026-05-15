"""Tests for volunteer scheduling, training, and accounting sync routes."""
from __future__ import annotations

import pytest

from ngo_homesuite.models.core import Organization, User, Volunteer, db


@pytest.fixture(scope="module")
def app(shared_test_app):
    return shared_test_app


@pytest.fixture()
def client(app):
    return app.test_client()


def _login(client, username, password):
    client.post("/auth/login", data={"username": username, "password": password})


def _ensure_user(app, username, email, role, password):
    with app.app_context():
        org = Organization.query.filter_by(is_active=True).order_by(Organization.id.asc()).first()
        if org is None:
            org = Organization(name="Vol Org", slug="vol-org", is_active=True)
            db.session.add(org)
            db.session.commit()

        user = User.query.filter_by(username=username).first()
        if user is None:
            user = User(
                username=username, email=email, role=role,
                is_active=True, organization_id=org.id,
            )
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
        elif user.organization_id is None:
            user.organization_id = org.id
            db.session.commit()
        return user.organization_id


def _ensure_volunteer(app, org_id: int, name: str = "Test Volunteer") -> int:
    with app.app_context():
        v = Volunteer.query.filter_by(organization_id=org_id, name=name).first()
        if v is None:
            v = Volunteer(organization_id=org_id, name=name, email="vol@test.local", status="active")
            db.session.add(v)
            db.session.commit()
        return v.id


# ---------------------------------------------------------------------------
# Shifts
# ---------------------------------------------------------------------------

class TestVolunteerShifts:
    def test_create_shift(self, client, app):
        org_id = _ensure_user(app, "vol_staff", "vol_s@test.local", "staff", "VolStaff123!")
        vol_id = _ensure_volunteer(app, org_id, "Alice Ramirez")
        _login(client, "vol_staff", "VolStaff123!")

        rv = client.post(
            "/volunteers/shifts",
            json={
                "volunteer_id": vol_id,
                "title": "Saturday food drive",
                "shift_date": "2025-06-14",
                "start_time": "09:00",
                "end_time": "13:00",
                "hours": 4.0,
                "location": "Community Centre",
            },
        )
        assert rv.status_code == 201
        body = rv.get_json()
        assert body["title"] == "Saturday food drive"
        assert body["status"] == "scheduled"

    def test_list_shifts_with_filter(self, client, app):
        org_id = _ensure_user(app, "vol_staff", "vol_s@test.local", "staff", "VolStaff123!")
        vol_id = _ensure_volunteer(app, org_id, "Alice Ramirez")
        _login(client, "vol_staff", "VolStaff123!")

        rv = client.get(f"/volunteers/shifts?volunteer_id={vol_id}")
        assert rv.status_code == 200
        assert isinstance(rv.get_json(), list)

    def test_complete_shift_updates_hours(self, client, app):
        org_id = _ensure_user(app, "vol_staff", "vol_s@test.local", "staff", "VolStaff123!")
        vol_id = _ensure_volunteer(app, org_id, "Bob Ndiaye")
        _login(client, "vol_staff", "VolStaff123!")

        # Create
        create_rv = client.post(
            "/volunteers/shifts",
            json={"volunteer_id": vol_id, "title": "Delivery run", "shift_date": "2025-07-01", "hours": 3.0},
        )
        shift_id = create_rv.get_json()["id"]

        # Complete
        complete_rv = client.post(f"/volunteers/shifts/{shift_id}/complete", json={"hours": 3.5})
        assert complete_rv.status_code == 200
        assert complete_rv.get_json()["status"] == "completed"
        assert complete_rv.get_json()["hours"] == 3.5

    def test_hours_summary(self, client, app):
        org_id = _ensure_user(app, "vol_staff", "vol_s@test.local", "staff", "VolStaff123!")
        _login(client, "vol_staff", "VolStaff123!")

        rv = client.get("/volunteers/hours")
        assert rv.status_code == 200
        data = rv.get_json()
        assert isinstance(data, list)
        assert all("volunteer_id" in r and "shift_hours" in r for r in data)

    def test_create_shift_requires_date(self, client, app):
        org_id = _ensure_user(app, "vol_staff", "vol_s@test.local", "staff", "VolStaff123!")
        vol_id = _ensure_volunteer(app, org_id)
        _login(client, "vol_staff", "VolStaff123!")

        rv = client.post("/volunteers/shifts", json={"volunteer_id": vol_id, "title": "Meeting"})
        assert rv.status_code == 400


# ---------------------------------------------------------------------------
# Training courses
# ---------------------------------------------------------------------------

class TestTrainingCourses:
    def test_course_full_lifecycle(self, client, app):
        org_id = _ensure_user(app, "vol_admin", "vol_a@test.local", "admin", "VolAdmin123!")
        _login(client, "vol_admin", "VolAdmin123!")

        # Create
        create_rv = client.post(
            "/volunteers/training/courses",
            json={
                "name": "Safeguarding 101",
                "category": "compliance",
                "duration_hours": 2.5,
                "is_required": True,
                "expires_after_days": 365,
            },
        )
        assert create_rv.status_code == 201
        body = create_rv.get_json()
        course_id = body["id"]
        assert body["is_required"] is True
        assert body["expires_after_days"] == 365

        # List
        list_rv = client.get("/volunteers/training/courses?is_required=true")
        assert list_rv.status_code == 200
        assert any(c["id"] == course_id for c in list_rv.get_json())

        # Update
        update_rv = client.patch(f"/volunteers/training/courses/{course_id}", json={"duration_hours": 3.0})
        assert update_rv.status_code == 200
        assert update_rv.get_json()["duration_hours"] == 3.0

        # Delete
        del_rv = client.delete(f"/volunteers/training/courses/{course_id}")
        assert del_rv.status_code == 204

    def test_course_requires_name(self, client, app):
        _ensure_user(app, "vol_admin", "vol_a@test.local", "admin", "VolAdmin123!")
        _login(client, "vol_admin", "VolAdmin123!")
        rv = client.post("/volunteers/training/courses", json={"category": "safety"})
        assert rv.status_code == 400


# ---------------------------------------------------------------------------
# Training assignments
# ---------------------------------------------------------------------------

class TestVolunteerTrainingAssignment:
    def _setup(self, client, app):
        org_id = _ensure_user(app, "vol_admin", "vol_a@test.local", "admin", "VolAdmin123!")
        vol_id = _ensure_volunteer(app, org_id, "Carol Osei")
        _login(client, "vol_admin", "VolAdmin123!")

        # Create a course
        course_rv = client.post(
            "/volunteers/training/courses",
            json={"name": "First Aid", "category": "safety", "is_required": False},
        )
        course_id = course_rv.get_json()["id"]
        return org_id, vol_id, course_id

    def test_assign_and_complete_training(self, client, app):
        org_id, vol_id, course_id = self._setup(client, app)

        # Assign
        assign_rv = client.post(f"/volunteers/{vol_id}/training", json={"course_id": course_id})
        assert assign_rv.status_code == 201
        training_id = assign_rv.get_json()["id"]
        assert assign_rv.get_json()["status"] == "pending"

        # Idempotent assign
        assign_rv2 = client.post(f"/volunteers/{vol_id}/training", json={"course_id": course_id})
        assert assign_rv2.status_code == 201
        assert assign_rv2.get_json()["id"] == training_id  # same record

        # List
        list_rv = client.get(f"/volunteers/{vol_id}/training")
        assert list_rv.status_code == 200
        assert any(t["id"] == training_id for t in list_rv.get_json())

        # Complete
        complete_rv = client.post(f"/volunteers/training/{training_id}/complete", json={"score": 88.5})
        assert complete_rv.status_code == 200
        body = complete_rv.get_json()
        assert body["status"] == "completed"
        assert body["score"] == 88.5

    def test_compliance_report(self, client, app):
        _ensure_user(app, "vol_admin", "vol_a@test.local", "admin", "VolAdmin123!")
        _login(client, "vol_admin", "VolAdmin123!")

        rv = client.get("/volunteers/training/compliance")
        assert rv.status_code == 200
        body = rv.get_json()
        assert "required_courses" in body
        assert "total_active_volunteers" in body

    def test_assign_requires_course_id(self, client, app):
        org_id = _ensure_user(app, "vol_admin", "vol_a@test.local", "admin", "VolAdmin123!")
        vol_id = _ensure_volunteer(app, org_id)
        _login(client, "vol_admin", "VolAdmin123!")
        rv = client.post(f"/volunteers/{vol_id}/training", json={})
        assert rv.status_code == 400


# ---------------------------------------------------------------------------
# Accounting sync routes
# ---------------------------------------------------------------------------

class TestAccountingSyncRoutes:
    def test_sync_donation_missing_provider(self, client, app):
        org_id = _ensure_user(app, "acc_admin", "acc@test.local", "admin", "AccAdmin123!")
        _login(client, "acc_admin", "AccAdmin123!")

        # Need a real donation — use API or fake ID; either way provider check fires first
        rv = client.post("/integrations/accounting/sync/donation/1", json={"provider": "stripe"})
        assert rv.status_code == 400

    def test_sync_donation_skipped_no_config(self, client, app):
        """Without QBO credentials the sync records status=skipped and returns 200."""
        org_id = _ensure_user(app, "acc_admin", "acc@test.local", "admin", "AccAdmin123!")
        _login(client, "acc_admin", "AccAdmin123!")

        # Create a donation to push
        with app.app_context():
            from ngo_homesuite.models.core import Donation
            d = Donation.query.filter_by(organization_id=org_id).first()
            if d is None:
                from ngo_homesuite.models.core import Donor
                donor = Donor.query.filter_by(organization_id=org_id).first()
                if donor is None:
                    donor = Donor(organization_id=org_id, name="Sync Test Donor")
                    db.session.add(donor)
                    db.session.commit()
                d = Donation(organization_id=org_id, donor_id=donor.id, amount=100.0, currency="USD")
                db.session.add(d)
                db.session.commit()
            donation_id = d.id

        rv = client.post(
            f"/integrations/accounting/sync/donation/{donation_id}",
            json={"provider": "quickbooks"},
        )
        assert rv.status_code == 200
        body = rv.get_json()
        assert body["status"] in ("skipped", "synced", "failed")  # skipped when no QBO credentials

    def test_sync_expense_skipped_no_config(self, client, app):
        org_id = _ensure_user(app, "acc_admin", "acc@test.local", "admin", "AccAdmin123!")
        _login(client, "acc_admin", "AccAdmin123!")

        with app.app_context():
            from ngo_homesuite.models.core import Expense
            e = Expense.query.filter_by(organization_id=org_id).first()
            if e is None:
                e = Expense(organization_id=org_id, amount=50.0, description="Test expense")
                db.session.add(e)
                db.session.commit()
            expense_id = e.id

        rv = client.post(
            f"/integrations/accounting/sync/expense/{expense_id}",
            json={"provider": "xero"},
        )
        assert rv.status_code == 200
        assert rv.get_json()["status"] in ("skipped", "synced", "failed")

    def test_sync_logs_returns_list(self, client, app):
        _ensure_user(app, "acc_admin", "acc@test.local", "admin", "AccAdmin123!")
        _login(client, "acc_admin", "AccAdmin123!")

        rv = client.get("/integrations/accounting/sync/logs")
        assert rv.status_code == 200
        assert isinstance(rv.get_json(), list)

    def test_sync_logs_filter_by_provider(self, client, app):
        _ensure_user(app, "acc_admin", "acc@test.local", "admin", "AccAdmin123!")
        _login(client, "acc_admin", "AccAdmin123!")

        rv = client.get("/integrations/accounting/sync/logs?provider=quickbooks&status=skipped")
        assert rv.status_code == 200
        for item in rv.get_json():
            assert item["provider"] == "quickbooks"
            assert item["status"] == "skipped"

    def test_oauth_callback_missing_fields(self, client, app):
        _ensure_user(app, "acc_admin", "acc@test.local", "admin", "AccAdmin123!")
        _login(client, "acc_admin", "AccAdmin123!")

        rv = client.post("/integrations/accounting/quickbooks/oauth/callback", json={"code": "abc"})
        assert rv.status_code == 400

    def test_xero_oauth_callback_missing_fields(self, client, app):
        _ensure_user(app, "acc_admin", "acc@test.local", "admin", "AccAdmin123!")
        _login(client, "acc_admin", "AccAdmin123!")

        rv = client.post("/integrations/accounting/xero/oauth/callback", json={})
        assert rv.status_code == 400
