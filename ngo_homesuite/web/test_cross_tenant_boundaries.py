"""
Cross-tenant boundary enforcement tests.

Each test proves that Org A's authenticated staff cannot read, mutate or
access Org B's data through any route.  Tests are negative-focused: they
assert 404 / 403 / empty-list responses, never a successful data leak.
"""
from __future__ import annotations

import pytest

from ngo_homesuite.models.core import (
    Beneficiary,
    Grant,
    MembershipRecord,
    MembershipTier,
    Organization,
    ProgramCase,
    User,
    db,
)


# ---------------------------------------------------------------------------
# Fixtures – two isolated orgs with one staff user each
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def app(shared_test_app):
    return shared_test_app


@pytest.fixture()
def client(app):
    return app.test_client()


def _make_org(app, name: str, slug: str) -> int:
    with app.app_context():
        org = Organization.query.filter_by(slug=slug).first()
        if org is None:
            org = Organization(name=name, slug=slug, is_active=True)
            db.session.add(org)
            db.session.commit()
        return org.id


def _make_user(app, username: str, email: str, role: str, password: str, org_id: int) -> None:
    with app.app_context():
        user = User.query.filter_by(username=username).first()
        if user is None:
            user = User(
                username=username,
                email=email,
                role=role,
                is_active=True,
                organization_id=org_id,
            )
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
        else:
            user.organization_id = org_id
            db.session.commit()


def _login(client, username: str, password: str) -> None:
    client.post("/auth/login", data={"username": username, "password": password})


def _logout(client) -> None:
    client.get("/auth/logout")


# ---------------------------------------------------------------------------
# Tests: grants
# ---------------------------------------------------------------------------

def test_grant_cross_tenant_read_isolation(client, app):
    """Org A staff cannot see Org B grants through the list endpoint."""
    org_a = _make_org(app, "CT Grant Org A", "ct-grant-org-a")
    org_b = _make_org(app, "CT Grant Org B", "ct-grant-org-b")
    _make_user(app, "ct_grant_a_staff", "ct_grant_a@test.local", "staff", "CTPass123!", org_a)
    _make_user(app, "ct_grant_b_staff", "ct_grant_b@test.local", "staff", "CTPass123!", org_b)

    # Org B creates a grant
    _login(client, "ct_grant_b_staff", "CTPass123!")
    rv = client.post("/grants/", json={"title": "Org B Secret Grant", "funder_name": "Foundation B"})
    assert rv.status_code == 201
    grant_b_id = rv.get_json()["id"]
    _logout(client)

    # Org A should not see it in their list
    _login(client, "ct_grant_a_staff", "CTPass123!")
    list_rv = client.get("/grants/")
    assert list_rv.status_code == 200
    ids = [g["id"] for g in list_rv.get_json()]
    assert grant_b_id not in ids

    # Org A cannot advance Org B's grant
    advance_rv = client.post(f"/grants/{grant_b_id}/advance", json={"new_status": "submitted"})
    assert advance_rv.status_code == 404

    # Org A cannot disburse Org B's grant
    disburse_rv = client.post(
        f"/grants/{grant_b_id}/disburse",
        json={"amount": 5000, "received_date": "2026-01-15"},
    )
    assert disburse_rv.status_code == 404
    _logout(client)


# ---------------------------------------------------------------------------
# Tests: membership
# ---------------------------------------------------------------------------

def test_membership_cross_tenant_tier_isolation(client, app):
    """Org A staff cannot see or operate on Org B membership tiers."""
    org_a = _make_org(app, "CT Mem Org A", "ct-mem-org-a")
    org_b = _make_org(app, "CT Mem Org B", "ct-mem-org-b")
    _make_user(app, "ct_mem_a_staff", "ct_mem_a@test.local", "staff", "CTPass123!", org_a)
    _make_user(app, "ct_mem_b_staff", "ct_mem_b@test.local", "staff", "CTPass123!", org_b)

    # Org B creates a tier
    _login(client, "ct_mem_b_staff", "CTPass123!")
    tier_rv = client.post("/membership/tiers", json={"name": "Org B Silver", "price": 99.0})
    assert tier_rv.status_code == 201
    tier_b_id = tier_rv.get_json()["id"]
    _logout(client)

    # Org A sees only their own tiers (not Org B's)
    _login(client, "ct_mem_a_staff", "CTPass123!")
    list_rv = client.get("/membership/tiers")
    assert list_rv.status_code == 200
    ids = [t["id"] for t in list_rv.get_json()]
    assert tier_b_id not in ids

    # Org A cannot enroll a member using Org B's tier
    enroll_rv = client.post(
        "/membership/members",
        json={"donor_id": 1, "tier_id": tier_b_id},
    )
    # 404 is the acceptable response — tier not found in this org
    assert enroll_rv.status_code in (404, 400)
    _logout(client)


def test_membership_member_renewal_cross_tenant(client, app):
    """Org A cannot renew a membership record that belongs to Org B."""
    org_a = _make_org(app, "CT Renew Org A", "ct-renew-org-a")
    org_b = _make_org(app, "CT Renew Org B", "ct-renew-org-b")
    _make_user(app, "ct_renew_a_staff", "ct_renew_a@test.local", "staff", "CTPass123!", org_a)
    _make_user(app, "ct_renew_b_staff", "ct_renew_b@test.local", "staff", "CTPass123!", org_b)

    # Org B sets up a tier + donor + membership record directly in DB
    with app.app_context():
        tier = MembershipTier(organization_id=org_b, name="CT Tier B", price=25.0, interval="yearly")
        db.session.add(tier)
        db.session.flush()
        from ngo_homesuite.models.core import Donor, MembershipRecord
        from datetime import date
        donor_b = Donor(organization_id=org_b, name="CT DonorB", email="ctdonorb@test.local")
        db.session.add(donor_b)
        db.session.flush()
        rec_b = MembershipRecord(
            organization_id=org_b,
            donor_id=donor_b.id,
            tier_id=tier.id,
            status="active",
            start_date=date(2026, 1, 1),
        )
        db.session.add(rec_b)
        db.session.commit()
        rec_b_id = rec_b.id

    _login(client, "ct_renew_a_staff", "CTPass123!")
    renew_rv = client.post(f"/membership/members/{rec_b_id}/renew")
    assert renew_rv.status_code == 404

    cancel_rv = client.post(f"/membership/members/{rec_b_id}/cancel")
    assert cancel_rv.status_code == 404
    _logout(client)


# ---------------------------------------------------------------------------
# Tests: programs / cases / beneficiaries
# ---------------------------------------------------------------------------

def test_program_case_cross_tenant_list_isolation(client, app):
    """Org A staff sees only their own cases and beneficiaries, not Org B's."""
    org_a = _make_org(app, "CT Prog Org A", "ct-prog-org-a")
    org_b = _make_org(app, "CT Prog Org B", "ct-prog-org-b")
    _make_user(app, "ct_prog_a_staff", "ct_prog_a@test.local", "staff", "CTPass123!", org_a)
    _make_user(app, "ct_prog_b_staff", "ct_prog_b@test.local", "staff", "CTPass123!", org_b)

    # Org B creates beneficiary + case
    _login(client, "ct_prog_b_staff", "CTPass123!")
    ben_rv = client.post(
        "/programs/intake/beneficiaries",
        json={"first_name": "CT", "last_name": "BenB", "program": "Health", "status": "active"},
    )
    assert ben_rv.status_code == 201
    ben_b_id = ben_rv.get_json()["id"]
    case_rv = client.post(
        "/programs/cases",
        json={"title": "CT Org B Case", "beneficiary_id": ben_b_id, "case_type": "service"},
    )
    assert case_rv.status_code == 201
    case_b_id = case_rv.get_json()["id"]
    _logout(client)

    # Org A list should not include Org B's case
    _login(client, "ct_prog_a_staff", "CTPass123!")
    cases_rv = client.get("/programs/cases")
    assert cases_rv.status_code == 200
    ids = [c["id"] for c in cases_rv.get_json()]
    assert case_b_id not in ids

    # Org A cannot retrieve activities for Org B's case
    activity_rv = client.get(f"/programs/cases/{case_b_id}/activities")
    assert activity_rv.status_code == 404

    # Org A cannot create a document on Org B's case
    doc_rv = client.post(
        f"/programs/cases/{case_b_id}/documents",
        json={"title": "Stolen doc", "category": "plan"},
    )
    assert doc_rv.status_code == 404
    _logout(client)


def test_program_beneficiary_cross_tenant_isolation(client, app):
    """Org A cannot see Org B beneficiaries in the list endpoint."""
    org_a = _make_org(app, "CT Ben Org A", "ct-ben-org-a")
    org_b = _make_org(app, "CT Ben Org B", "ct-ben-org-b")
    _make_user(app, "ct_ben_a_staff", "ct_ben_a@test.local", "staff", "CTPass123!", org_a)
    _make_user(app, "ct_ben_b_staff", "ct_ben_b@test.local", "staff", "CTPass123!", org_b)

    _login(client, "ct_ben_b_staff", "CTPass123!")
    b_rv = client.post(
        "/programs/intake/beneficiaries",
        json={"first_name": "Secret", "last_name": "PersonB", "program": "Housing", "status": "active"},
    )
    assert b_rv.status_code == 201
    ben_b_id = b_rv.get_json()["id"]
    _logout(client)

    _login(client, "ct_ben_a_staff", "CTPass123!")
    list_rv = client.get("/programs/intake/beneficiaries")
    assert list_rv.status_code == 200
    ids = [b["id"] for b in list_rv.get_json()]
    assert ben_b_id not in ids
    _logout(client)


# ---------------------------------------------------------------------------
# Tests: viewer role cannot mutate
# ---------------------------------------------------------------------------

def test_viewer_cannot_create_grant(client, app):
    org = _make_org(app, "CT Viewer Org", "ct-viewer-org")
    _make_user(app, "ct_viewer_v1", "ct_viewer_v1@test.local", "viewer", "CTPass123!", org)
    _login(client, "ct_viewer_v1", "CTPass123!")

    rv = client.post("/grants/", json={"title": "Viewer Grant", "funder_name": "Foundation"})
    assert rv.status_code == 403
    _logout(client)


def test_viewer_cannot_create_membership_tier(client, app):
    org = _make_org(app, "CT Viewer Mem Org", "ct-viewer-mem-org")
    _make_user(app, "ct_viewer_v2", "ct_viewer_v2@test.local", "viewer", "CTPass123!", org)
    _login(client, "ct_viewer_v2", "CTPass123!")

    rv = client.post("/membership/tiers", json={"name": "Viewer Tier", "price": 10.0})
    assert rv.status_code == 403
    _logout(client)


def test_viewer_cannot_create_program_case(client, app):
    org = _make_org(app, "CT Viewer Prog Org", "ct-viewer-prog-org")
    _make_user(app, "ct_viewer_v3", "ct_viewer_v3@test.local", "viewer", "CTPass123!", org)
    _login(client, "ct_viewer_v3", "CTPass123!")

    rv = client.post(
        "/programs/cases",
        json={"title": "Viewer Case", "case_type": "service"},
    )
    assert rv.status_code == 403
    _logout(client)


def test_viewer_cannot_dispatch_reminders(client, app):
    org = _make_org(app, "CT Viewer Reminder Org", "ct-viewer-reminder-org")
    _make_user(app, "ct_viewer_v4", "ct_viewer_v4@test.local", "viewer", "CTPass123!", org)

    # Need a case_id (use 99999 — will 404 before RBAC check)
    _login(client, "ct_viewer_v4", "CTPass123!")
    rv = client.post("/programs/cases/99999/followups/dispatch-reminders", json={})
    assert rv.status_code == 403
    _logout(client)


# ---------------------------------------------------------------------------
# Tests: unauthenticated user cannot access any authenticated route
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("method,path", [
    ("GET",  "/grants/"),
    ("POST", "/grants/"),
    ("GET",  "/membership/tiers"),
    ("POST", "/membership/tiers"),
    ("GET",  "/programs/cases"),
    ("POST", "/programs/cases"),
    ("GET",  "/volunteers/shifts"),
    ("GET",  "/reports"),
    ("GET",  "/admin/users"),
])
def test_unauthenticated_redirected_to_login(client, method, path):
    rv = client.open(path, method=method, follow_redirects=False)
    # Must get 302 → /auth/login or 401, never a 200
    assert rv.status_code in (302, 401), f"{method} {path} returned {rv.status_code}"
    if rv.status_code == 302:
        assert "/auth/login" in (rv.headers.get("Location") or "")
