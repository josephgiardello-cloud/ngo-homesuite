from __future__ import annotations

import pytest

from ngo_homesuite.models.core import Donation, Donor, Organization, User, db


@pytest.fixture(scope="module")
def app(shared_test_app):
    return shared_test_app


@pytest.fixture()
def client(app):
    return app.test_client()


def _login(client, username: str, password: str) -> None:
    client.post("/auth/login", data={"username": username, "password": password})


def test_donor_delete_ignores_other_org_donations(client, app):
    with app.app_context():
        org_a = Organization(name="Delete Org A", slug="delete-org-a", is_active=True)
        org_b = Organization(name="Delete Org B", slug="delete-org-b", is_active=True)
        db.session.add_all([org_a, org_b])
        db.session.flush()
        org_a_id = int(org_a.id)
        org_b_id = int(org_b.id)

        user = User(
            username="delete_org_admin",
            email="delete.org.admin@test.local",
            role="admin",
            is_active=True,
            organization_id=org_a_id,
        )
        user.set_password("delete_pass_123")
        db.session.add(user)
        db.session.flush()

        donor = Donor(
            organization_id=org_a_id,
            name="Delete Me",
            email="delete.me@example.org",
            donor_type="individual",
        )
        db.session.add(donor)
        db.session.flush()

        stray_donation = Donation(
            organization_id=org_b_id,
            donor_id=donor.id,
            donor_name=donor.name,
            donor_email=donor.email,
            amount=50.0,
            currency="USD",
            status="received",
        )
        db.session.add(stray_donation)
        db.session.commit()
        donor_id = donor.id

    _login(client, "delete_org_admin", "delete_pass_123")

    rv = client.post(f"/donors/{donor_id}/delete", follow_redirects=True)
    assert rv.status_code == 200
    assert "Donor deleted successfully." in rv.get_data(as_text=True)

    with app.app_context():
        assert Donor.query.filter_by(id=donor_id, organization_id=org_a_id).first() is None
