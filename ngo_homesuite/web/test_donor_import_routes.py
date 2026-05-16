from __future__ import annotations

from io import BytesIO
import uuid

import pytest

from ngo_homesuite.models.core import Donor, Organization, User, db


@pytest.fixture(scope="module")
def app(shared_test_app):
    return shared_test_app


@pytest.fixture()
def client(app):
    return app.test_client()


def _login(client, username: str, password: str) -> None:
    client.post("/auth/login", data={"username": username, "password": password})


def _ensure_admin(app) -> tuple[int, str, str]:
    with app.app_context():
        suffix = uuid.uuid4().hex[:8]
        org = Organization(name="Import Org", slug="import-org", is_active=True)
        org.slug = f"import-org-{suffix}"
        db.session.add(org)
        db.session.flush()

        username = f"import_admin_{suffix}"
        user = User(
            username=username,
            email=f"{username}@test.local",
            role="admin",
            is_active=True,
            organization_id=org.id,
        )
        user.set_password("import_pass_123")
        db.session.add(user)
        db.session.commit()
        return int(org.id), username, "import_pass_123"


def test_donor_import_preview_and_apply_creates_ready_rows(client, app):
    org_id, username, password = _ensure_admin(app)
    _login(client, username, password)

    csv_payload = (
        "Full Name,Email Address,Phone Number,Category,Comments\n"
        "Ada Lovelace,ada@example.org,555-111-0000,individual,Major donor\n"
        "Grace Hopper,grace@example.org,555-111-0001,corporate,Potential sponsor\n"
    ).encode("utf-8")

    preview = client.post(
        "/donors/import",
        data={
            "action": "preview",
            "import_file": (BytesIO(csv_payload), "donors.csv"),
        },
        content_type="multipart/form-data",
    )
    assert preview.status_code == 200
    body = preview.get_data(as_text=True)
    assert "Import Ready Rows" in body
    assert "name=\"cache_id\"" in body

    cache_marker = "name=\"cache_id\" value=\""
    cache_id = body.split(cache_marker, 1)[1].split('"', 1)[0]

    apply = client.post(
        "/donors/import",
        data={
            "action": "import",
            "cache_id": cache_id,
            "map_name": "Full Name",
            "map_email": "Email Address",
            "map_phone": "Phone Number",
            "map_donor_type": "Category",
            "map_notes": "Comments",
        },
        follow_redirects=True,
    )
    assert apply.status_code == 200
    assert "Import completed:" in apply.get_data(as_text=True)

    with app.app_context():
        donors = Donor.query.filter_by(organization_id=org_id).all()
        assert any(d.name == "Ada Lovelace" for d in donors)
        assert any(d.name == "Grace Hopper" for d in donors)


def test_donor_import_preview_marks_duplicates(client, app):
    org_id, username, password = _ensure_admin(app)

    with app.app_context():
        donor = Donor(
            organization_id=org_id,
            name="Existing Donor",
            email="existing@example.org",
            donor_type="individual",
        )
        db.session.add(donor)
        db.session.commit()

    _login(client, username, password)

    csv_payload = (
        "name,email\n"
        "Existing Donor,existing@example.org\n"
    ).encode("utf-8")

    preview = client.post(
        "/donors/import",
        data={
            "action": "preview",
            "import_file": (BytesIO(csv_payload), "dupes.csv"),
        },
        content_type="multipart/form-data",
    )
    assert preview.status_code == 200
    body = preview.get_data(as_text=True)
    assert "duplicate" in body.lower()
    assert "Duplicate of" in body
