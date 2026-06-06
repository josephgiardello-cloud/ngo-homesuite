from __future__ import annotations

import pytest
import yaml

from ngo_homesuite.models.core import User, db


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
        user = User.query.filter_by(username=username).first()
        if user is None:
            user = User(username=username, email=email, role=role, is_active=True)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()


def test_api_docs_requires_auth(client):
    rv = client.get("/api/docs", follow_redirects=False)
    assert rv.status_code in (302, 303)
    swagger_rv = client.get("/api/swagger", follow_redirects=False)
    assert swagger_rv.status_code in (302, 303)


def test_api_docs_and_openapi_endpoints_return_content(client, app):
    _ensure_user(app, "api_docs_viewer", "api_docs_viewer@test.local", "viewer", "viewer_docs_pass_123")
    _login(client, "api_docs_viewer", "viewer_docs_pass_123")

    docs_rv = client.get("/api/docs")
    assert docs_rv.status_code == 200
    body = docs_rv.get_data(as_text=True)
    assert "NGO HomeSuite API Docs" in body
    assert "/api/openapi.yaml" in body
    assert "/api/swagger" in body

    swagger_rv = client.get("/api/swagger")
    assert swagger_rv.status_code == 200
    swagger_body = swagger_rv.get_data(as_text=True)
    assert "SwaggerUIBundle" in swagger_body
    assert "/api/openapi.yaml" in swagger_body

    spec_rv = client.get("/api/openapi.yaml")
    assert spec_rv.status_code == 200
    spec_text = spec_rv.get_data(as_text=True)
    assert "openapi: 3.0.3" in spec_text
    assert "/ai/minion/chat" in spec_text
    assert "/api/swagger" in spec_text
    assert "operationId: postMinionChat" in spec_text
    assert "operationId: getComplianceEvidence" in spec_text
    assert "MinionChatRequest:" in spec_text
    assert "MinionChatResponse:" in spec_text
    assert "ErrorResponse:" in spec_text

    spec_obj = yaml.safe_load(spec_text)
    assert isinstance(spec_obj, dict)
    assert isinstance(spec_obj.get("paths"), dict)

    operation_ids = []
    for methods in spec_obj["paths"].values():
        if not isinstance(methods, dict):
            continue
        for operation in methods.values():
            if not isinstance(operation, dict):
                continue
            operation_id = operation.get("operationId")
            assert operation_id, "Every operation must define operationId"
            operation_ids.append(operation_id)

    assert len(operation_ids) == len(set(operation_ids)), "operationId values must be unique"

