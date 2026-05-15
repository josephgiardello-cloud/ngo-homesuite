from __future__ import annotations

from dataclasses import dataclass

import pytest

from ngo_homesuite.app_factory import create_app
from ngo_homesuite.flask_config import TestingConfig
from ngo_homesuite.models.core import User, db


@dataclass
class _FakeResponse:
    answer: str
    sources: list
    actions: list
    redactions: int


class _FakeCopilot:
    last_kwargs = None

    def answer(self, **kwargs):
        _FakeCopilot.last_kwargs = kwargs
        return _FakeResponse(
            answer="Copilot response",
            sources=[{"source": "docs/sprint1_backlog.md", "text": "demo"}],
            actions=[],
            redactions=0,
        )

    def reindex(self, user_summary_texts=None):
        return 42


@pytest.fixture(scope="module")
def app():
    class _TestCfg(TestingConfig):
        COPILOT_ENABLED = True

    return create_app(_TestCfg)


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


def test_copilot_chat_endpoint_returns_payload(client, app, monkeypatch):
    _ensure_user(app, "copilot_admin", "copilot_admin@test.local", "admin", "admin_pass_123")
    _login(client, "copilot_admin", "admin_pass_123")

    monkeypatch.setattr("ngo_homesuite.web.ai_routes.HomeSuiteCopilot.from_app", lambda: _FakeCopilot())

    rv = client.post(
        "/ai/copilot/chat",
        json={"prompt": "How do I generate reports?", "context": {"active_page": "reports"}},
    )
    assert rv.status_code == 200
    data = rv.get_json()
    assert data["response"] == "Copilot response"
    assert isinstance(data["sources"], list)
    assert data["mode"] == "copilot"


def test_copilot_reindex_admin_only(client, app, monkeypatch):
    _ensure_user(app, "copilot_viewer", "copilot_viewer@test.local", "viewer", "viewer_pass_123")
    _login(client, "copilot_viewer", "viewer_pass_123")
    rv = client.post("/ai/copilot/reindex", json={"user_summaries": ["summary"]})
    assert rv.status_code == 403
    client.get("/auth/logout")

    _ensure_user(app, "copilot_admin2", "copilot_admin2@test.local", "admin", "admin2_pass_123")
    _login(client, "copilot_admin2", "admin2_pass_123")

    monkeypatch.setattr("ngo_homesuite.web.ai_routes.HomeSuiteCopilot.from_app", lambda: _FakeCopilot())

    rv = client.post("/ai/copilot/reindex", json={"user_summaries": ["summary"]})
    assert rv.status_code == 200
    data = rv.get_json()
    assert data["ok"] is True
    assert data["chunks_indexed"] == 42


def test_copilot_chat_forwards_action_gating_inputs(client, app, monkeypatch):
    _ensure_user(app, "copilot_staff", "copilot_staff@test.local", "staff", "staff_pass_123")
    _login(client, "copilot_staff", "staff_pass_123")

    monkeypatch.setattr("ngo_homesuite.web.ai_routes.HomeSuiteCopilot.from_app", lambda: _FakeCopilot())

    rv = client.post(
        "/ai/copilot/chat",
        json={
            "prompt": "Create a donor called Jane",
            "allow_actions": True,
            "approved_actions": ["create_donor"],
            "tool_allowlist": ["create_donor", "search_donors"],
        },
    )
    assert rv.status_code == 200

    passed = _FakeCopilot.last_kwargs
    assert passed is not None
    runtime_ctx = passed["runtime_ctx"]
    assert runtime_ctx["approved_actions"] == ["create_donor"]
    assert runtime_ctx["tool_allowlist"] == ["create_donor", "search_donors"]


def test_copilot_chat_route_allowlist_is_constrained_by_config(client, app, monkeypatch):
    _ensure_user(app, "copilot_staff_cfg", "copilot_staff_cfg@test.local", "staff", "staff_pass_cfg_123")
    _login(client, "copilot_staff_cfg", "staff_pass_cfg_123")

    monkeypatch.setattr("ngo_homesuite.web.ai_routes.HomeSuiteCopilot.from_app", lambda: _FakeCopilot())

    old_cfg = app.config.get("COPILOT_TOOL_ALLOWLIST")
    app.config["COPILOT_TOOL_ALLOWLIST"] = "search_donors"
    try:
        rv = client.post(
            "/ai/copilot/chat",
            json={
                "prompt": "Create a donor called Jane",
                "allow_actions": True,
                "approved_actions": ["create_donor", "search_donors"],
                "tool_allowlist": ["create_donor", "search_donors"],
            },
        )
        assert rv.status_code == 200

        passed = _FakeCopilot.last_kwargs
        assert passed is not None
        runtime_ctx = passed["runtime_ctx"]
        assert runtime_ctx["tool_allowlist"] == ["search_donors"]
        assert runtime_ctx["approved_actions"] == ["search_donors"]
    finally:
        app.config["COPILOT_TOOL_ALLOWLIST"] = old_cfg
