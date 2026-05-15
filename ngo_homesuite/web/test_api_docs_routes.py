from __future__ import annotations

import pytest

from ngo_homesuite.app_factory import create_app
from ngo_homesuite.flask_config import TestingConfig
from ngo_homesuite.models.core import User, db


@pytest.fixture(scope="module")
def app():
    return create_app(TestingConfig)


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


def test_api_docs_and_openapi_endpoints_return_content(client, app):
    _ensure_user(app, "api_docs_viewer", "api_docs_viewer@test.local", "viewer", "viewer_docs_pass_123")
    _login(client, "api_docs_viewer", "viewer_docs_pass_123")

    docs_rv = client.get("/api/docs")
    assert docs_rv.status_code == 200
    body = docs_rv.get_data(as_text=True)
    assert "NGO HomeSuite API Docs" in body
    assert "/api/openapi.yaml" in body

    spec_rv = client.get("/api/openapi.yaml")
    assert spec_rv.status_code == 200
    spec_text = spec_rv.get_data(as_text=True)
    assert "openapi: 3.0.3" in spec_text
    assert "/ai/copilot/chat" in spec_text
