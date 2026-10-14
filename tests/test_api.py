from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest

from backend.app.api import create_app
from tests.conftest import TEST_PASSWORD


@pytest.fixture
def client(system):
    with TestClient(create_app(system.store, secure_cookies=False)) as client:
        yield client


def sign_in(client, username="admin", tenant="alpha"):
    response = client.post("/api/v1/login", json={
        "tenant": tenant, "username": username, "password": TEST_PASSWORD,
    })
    assert response.status_code == 200, response.text
    client.headers["x-csrf-token"] = response.json()["csrf_token"]
    return response


def test_api_contract_shared_path_idempotency_and_shapes(client, system):
    sign_in(client, "fundraiser")
    donor = client.post("/api/v1/donors", json={"name": "API donor"})
    assert donor.status_code == 201 and set(donor.json()) == {"id"}
    body = {"donor_id": donor.json()["id"], "amount": "20.00", "idempotency_key": "api-receipt"}
    with patch.object(client.app.state.services, "record_donation",
                      wraps=client.app.state.services.record_donation) as shared:
        first = client.post("/api/v1/donations", json=body)
        retry = client.post("/api/v1/donations", json=body)
        assert shared.call_count == 2
    assert first.status_code == retry.status_code == 201
    assert first.json() == retry.json()
    changed = client.post("/api/v1/donations", json={**body, "amount": "21"})
    assert changed.status_code == 409 and set(changed.json()) == {"detail"}
    response = client.put(f"/api/v1/donations/{first.json()['id']}/allocations", json={
        "allocations": [{"category": "general", "amount": "20"}],
    })
    assert response.status_code == 200 and response.json() == {"ok": True}
    assert len(client.get("/api/v1/donations").json()) == 1
    assert client.post("/api/v1/donations", json={**body, "tenant": "beta"}).status_code == 422
    assert client.post("/api/v1/donations", json={**body, "amount": 20}).status_code == 422


def test_permissions_csrf_origin_and_tenant_cookie_binding(client, system):
    assert client.get("/api/v1/donors").status_code == 401
    sign_in(client, "viewer")
    assert client.get("/api/v1/donors").status_code == 200
    assert client.post("/api/v1/donors", json={"name": "Forbidden"}).status_code == 403
    assert client.get("/api/v1/audit_log").status_code == 403
    sign_in(client)
    token = client.cookies.get("ngohs_session")
    client.headers.pop("x-csrf-token")
    assert client.post("/api/v1/donors", json={"name": "CSRF"}).status_code == 403
    sign_in(client)
    assert client.post("/api/v1/donors", json={"name": "Origin"},
                       headers={"origin": "https://untrusted.example"}).status_code == 403
    client.cookies.clear()
    client.cookies.set("ngohs_session", token.replace("alpha.", "beta.", 1))
    assert client.get("/api/v1/donors").status_code == 401
    sign_in(client, tenant="beta")
    assert client.get("/api/v1/donors").json() == []
    assert client.post("/api/v1/donations", json={
        "donor_id": system.services.create_donor(system.admin, "Private"),
        "amount": "20", "idempotency_key": "cross-tenant",
    }).status_code == 404


def test_two_admin_grant_workflow_via_api(client):
    sign_in(client)
    project = client.post("/api/v1/projects", json={"name": "Project", "budget": "100"}).json()["id"]
    grant = client.post("/api/v1/grants", json={
        "project_id": project, "name": "Grant", "amount": "75",
    }).json()["id"]
    endpoint = f"/api/v1/grants/{grant}/transition"
    assert client.post(endpoint, json={"action": "approve"}).json() == {"ok": True}
    assert client.post(endpoint, json={"action": "pay"}).status_code == 403
    sign_in(client, "payer")
    assert client.post(endpoint, json={"action": "pay"}).status_code == 200
    assert client.post(endpoint, json={"action": "cancel"}).status_code == 409
    assert client.get("/api/v1/projects").json()[0]["spent"] == 75
    assert client.post("/api/v1/logout").json() == {"ok": True}
    assert client.get("/api/v1/session").status_code == 401


def test_login_backoff_persists_and_session_revocation(client, system):
    wrong = {"tenant": "alpha", "username": "admin", "password": TEST_PASSWORD + "-incorrect"}
    assert client.post("/api/v1/login", json=wrong).status_code == 401
    assert client.post("/api/v1/login", json=wrong).status_code == 429
    with system.store.transaction("alpha", write=True) as conn:
        conn.execute("DELETE FROM login_attempts")
    sign_in(client)
    with system.store.transaction("alpha", write=True) as conn:
        conn.execute("UPDATE users SET role='viewer' WHERE id=?", (system.admin.user_id,))
    assert client.post("/api/v1/projects", json={"name": "Revoked", "budget": "100"}).status_code == 403


def test_secure_cookie_and_openapi_v1_contract(system):
    with TestClient(create_app(system.store)) as secure:
        response = sign_in(secure)
        cookie = response.headers["set-cookie"].lower()
        assert "httponly" in cookie and "secure" in cookie and "samesite=strict" in cookie
        spec = secure.get("/openapi.json").json()
        expected = {
            "/api/v1/login": {"post"}, "/api/v1/logout": {"post"}, "/api/v1/session": {"get"},
            "/api/v1/{entity}": {"get"}, "/api/v1/donors": {"post"},
            "/api/v1/donations": {"post"}, "/api/v1/donations/{donation_id}/allocations": {"put"},
            "/api/v1/projects": {"post"}, "/api/v1/projects/{project_id}/spent": {"put"},
            "/api/v1/grants": {"post"}, "/api/v1/grants/{grant_id}/transition": {"post"},
            "/api/v1/users": {"post"}, "/health": {"get"},
        }
        assert {path: set(methods) for path, methods in spec["paths"].items()} == expected
        schemas = spec["components"]["schemas"]
        assert set(schemas["IDOutput"]["properties"]) == {"id"}
        assert schemas["DonationInput"]["required"] == ["donor_id", "amount", "idempotency_key"]
        assert schemas["DonationInput"]["properties"]["amount"]["type"] == "string"
        assert schemas["GrantActionInput"]["properties"]["action"]["enum"] == ["approve", "pay", "cancel"]
