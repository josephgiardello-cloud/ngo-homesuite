from __future__ import annotations

from unittest.mock import patch

import pytest

from ngo_homesuite.app_factory import create_app
from ngo_homesuite.flask_config import TestingConfig
from ngo_homesuite.models.core import Organization, User, db


@pytest.fixture(scope="module")
def app():
    class _TestCfg(TestingConfig):
        COPILOT_ENABLED = True
        METRICS_ENABLED = True
        ROLES_REQUIRING_2FA = []

    return create_app(_TestCfg)


@pytest.fixture()
def client(app):
    return app.test_client()


def _ensure_user(app, username: str, email: str, role: str, password: str) -> tuple[int, int]:
    with app.app_context():
        org = Organization.query.filter_by(is_active=True).first()
        assert org is not None

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

        assert user.id is not None
        return int(user.id), int(org.id)


def _login(client, username: str, password: str) -> None:
    client.post("/auth/login", data={"username": username, "password": password})


def _extract_request_log_entry(log_calls, target_path: str):
    for call in log_calls:
        if not call.args:
            continue
        if call.args[0] != "request_completed":
            continue
        extra = call.kwargs.get("extra") or {}
        extra_fields = extra.get("extra_fields") or {}
        if extra_fields.get("path") == target_path:
            return extra_fields
    return None


def test_request_completed_log_contains_structured_request_fields(client, app):
    actor_id, org_id = _ensure_user(
        app,
        "obs_log_admin",
        "obs_log_admin@test.local",
        "admin",
        "obs_log_admin_pass_123",
    )
    request_id = "req-structured-log-001"

    with patch.object(app.logger, "info") as log_info:
        _login(client, "obs_log_admin", "obs_log_admin_pass_123")
        rv = client.get("/api/v1/workflows", headers={"X-Request-ID": request_id})

    assert rv.status_code == 200

    entry = _extract_request_log_entry(log_info.call_args_list, "/api/v1/workflows")
    assert entry is not None

    assert entry["request_id"] == request_id
    assert entry["org_id"] == org_id
    assert entry["actor_id"] == actor_id
    assert entry["method"] == "GET"
    assert entry["path"] == "/api/v1/workflows"
    assert entry["status_code"] == 200
    assert entry["status"] == "2xx"
    assert isinstance(entry["duration_ms"], float)
    assert entry["latency_bucket"] in {
        "lt_50ms",
        "50_to_199ms",
        "200_to_999ms",
        "gte_1000ms",
    }
