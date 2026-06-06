from __future__ import annotations

from dataclasses import dataclass

import pytest

from ngo_homesuite.app_factory import create_app
from ngo_homesuite.flask_config import TestingConfig
from ngo_homesuite.models.core import AIConversation, AIMessage, Organization, User, db


@dataclass
class _FakeResponse:
    answer: str
    sources: list
    actions: list
    redactions: int


class _FakeMinion:
    last_kwargs = None

    def answer(self, **kwargs):
        _FakeMinion.last_kwargs = kwargs
        return _FakeResponse(
            answer="Minion response",
            sources=[{"source": "docs/sprint1_backlog.md", "text": "demo"}],
            actions=[],
            redactions=0,
        )

    def reindex(self, user_summary_texts=None):
        return 42


class _FakeMinionPending:
    last_kwargs = None

    def answer(self, **kwargs):
        _FakeMinionPending.last_kwargs = kwargs
        return _FakeResponse(
            answer="Approval required.",
            sources=[],
            actions=[
                {
                    "tool": "create_donor",
                    "args": {"name": "Jane"},
                    "status": "pending_approval",
                    "reason": "explicit_approval_required",
                }
            ],
            redactions=0,
        )

    def reindex(self, user_summary_texts=None):
        return 1


@pytest.fixture(scope="module")
def app():
    class _TestCfg(TestingConfig):
        MINION_ENABLED = True
        ROLES_REQUIRING_2FA = []

    return create_app(_TestCfg)


@pytest.fixture()
def client(app):
    return app.test_client()


def _login(client, username: str, password: str) -> None:
    client.post("/auth/login", data={"username": username, "password": password})


def _ensure_user(app, username: str, email: str, role: str, password: str):
    with app.app_context():
        user = User.query.filter_by(username=username).first()
        org = Organization.query.filter_by(is_active=True).first()
        if user is None:
            user = User(
                username=username,
                email=email,
                role=role,
                is_active=True,
                organization_id=org.id if org else None,
            )
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
        elif user.organization_id is None and org is not None:
            user.organization_id = org.id
            db.session.commit()


def test_minion_chat_endpoint_returns_payload(client, app, monkeypatch):
    _ensure_user(app, "minion_admin", "minion_admin@test.local", "admin", "admin_pass_123")
    _login(client, "minion_admin", "admin_pass_123")

    monkeypatch.setattr("ngo_homesuite.web.ai_routes.HomeSuiteMinion.from_app", lambda: _FakeMinion())

    rv = client.post(
        "/ai/minion/chat",
        json={"prompt": "How do I generate reports?", "context": {"active_page": "reports"}},
    )
    assert rv.status_code == 200
    data = rv.get_json()
    assert data["response"] == "Minion response"
    assert isinstance(data["sources"], list)
    assert data["mode"] == "minion"


def test_minion_reindex_admin_only(client, app, monkeypatch):
    _ensure_user(app, "minion_viewer", "minion_viewer@test.local", "viewer", "viewer_pass_123")
    _login(client, "minion_viewer", "viewer_pass_123")
    rv = client.post("/ai/minion/reindex", json={"user_summaries": ["summary"]})
    assert rv.status_code == 403
    client.post("/auth/logout")

    _ensure_user(app, "minion_admin2", "minion_admin2@test.local", "admin", "admin2_pass_123")
    _login(client, "minion_admin2", "admin2_pass_123")

    monkeypatch.setattr("ngo_homesuite.web.ai_routes.HomeSuiteMinion.from_app", lambda: _FakeMinion())

    rv = client.post("/ai/minion/reindex", json={"user_summaries": ["summary"]})
    assert rv.status_code == 200
    data = rv.get_json()
    assert data["ok"] is True
    assert data["chunks_indexed"] == 42


def test_minion_chat_forwards_action_gating_inputs(client, app, monkeypatch):
    _ensure_user(app, "minion_staff", "minion_staff@test.local", "staff", "staff_pass_123")
    _login(client, "minion_staff", "staff_pass_123")

    monkeypatch.setattr("ngo_homesuite.web.ai_routes.HomeSuiteMinion.from_app", lambda: _FakeMinion())

    monkeypatch.setattr("ngo_homesuite.web.ai_routes._verify_approval_token", lambda **kwargs: True)

    rv = client.post(
        "/ai/minion/chat",
        json={
            "prompt": "Create a donor called Jane",
            "allow_actions": True,
            "approved_actions": [{"tool": "create_donor", "token": "tok-123"}],
            "tool_allowlist": ["create_donor", "search_donors"],
        },
    )
    assert rv.status_code == 200

    passed = _FakeMinion.last_kwargs
    assert passed is not None
    runtime_ctx = passed["runtime_ctx"]
    assert runtime_ctx["approved_actions"] == ["create_donor"]
    assert runtime_ctx["tool_allowlist"] == ["create_donor", "search_donors"]


def test_minion_chat_route_allowlist_is_constrained_by_config(client, app, monkeypatch):
    _ensure_user(app, "minion_staff_cfg", "minion_staff_cfg@test.local", "staff", "staff_pass_cfg_123")
    _login(client, "minion_staff_cfg", "staff_pass_cfg_123")

    monkeypatch.setattr("ngo_homesuite.web.ai_routes.HomeSuiteMinion.from_app", lambda: _FakeMinion())

    old_cfg = app.config.get("MINION_TOOL_ALLOWLIST")
    app.config["MINION_TOOL_ALLOWLIST"] = "search_donors"
    try:
        monkeypatch.setattr("ngo_homesuite.web.ai_routes._verify_approval_token", lambda **kwargs: True)

        rv = client.post(
            "/ai/minion/chat",
            json={
                "prompt": "Create a donor called Jane",
                "allow_actions": True,
                "approved_actions": [
                    {"tool": "create_donor", "token": "tok-a"},
                    {"tool": "search_donors", "token": "tok-b"},
                ],
                "tool_allowlist": ["create_donor", "search_donors"],
            },
        )
        assert rv.status_code == 200

        passed = _FakeMinion.last_kwargs
        assert passed is not None
        runtime_ctx = passed["runtime_ctx"]
        assert runtime_ctx["tool_allowlist"] == ["search_donors"]
        assert runtime_ctx["approved_actions"] == ["search_donors"]
    finally:
        app.config["MINION_TOOL_ALLOWLIST"] = old_cfg


def test_minion_chat_pending_action_includes_approval_token(client, app, monkeypatch):
    _ensure_user(app, "minion_admin_token", "minion_admin_token@test.local", "admin", "admin_token_pass_123")
    _login(client, "minion_admin_token", "admin_token_pass_123")

    monkeypatch.setattr("ngo_homesuite.web.ai_routes.HomeSuiteMinion.from_app", lambda: _FakeMinionPending())

    rv = client.post(
        "/ai/minion/chat",
        json={"prompt": "Create donor Jane", "allow_actions": True},
    )
    assert rv.status_code == 200
    data = rv.get_json()
    assert isinstance(data.get("actions"), list)
    assert data["actions"][0]["status"] == "pending_approval"
    assert isinstance(data["actions"][0].get("approval_token"), str)
    assert data["actions"][0]["approval_token"]


def test_minion_chat_rejects_approval_without_valid_token(client, app, monkeypatch):
    _ensure_user(app, "minion_admin_token2", "minion_admin_token2@test.local", "admin", "admin_token2_pass_123")
    _login(client, "minion_admin_token2", "admin_token2_pass_123")

    monkeypatch.setattr("ngo_homesuite.web.ai_routes.HomeSuiteMinion.from_app", lambda: _FakeMinion())
    monkeypatch.setattr("ngo_homesuite.web.ai_routes._verify_approval_token", lambda **kwargs: False)

    rv = client.post(
        "/ai/minion/chat",
        json={
            "prompt": "Create donor Jane",
            "allow_actions": True,
            "approved_actions": [{"tool": "create_donor", "token": "bad-token"}],
            "tool_allowlist": ["create_donor"],
        },
    )
    assert rv.status_code == 200
    passed = _FakeMinion.last_kwargs
    assert passed is not None
    runtime_ctx = passed["runtime_ctx"]
    assert runtime_ctx["approved_actions"] == []


def test_ai_insights_query_endpoint_returns_report(client, app, monkeypatch):
    _ensure_user(app, "insights_staff", "insights_staff@test.local", "staff", "insights_staff_pass_123")
    _login(client, "insights_staff", "insights_staff_pass_123")

    monkeypatch.setattr(
        "ngo_homesuite.web.ai_routes.AIInsightsService.natural_language_report",
        lambda org_id, query, limit=10, project_root=None: {
            "query": query,
            "sections": [
                {
                    "title": "Predictive donor churn and lifetime value",
                    "summary": "demo",
                    "items": [{"donor_id": 1, "churn_risk": 0.72}],
                }
            ],
        },
    )

    rv = client.post("/ai/insights/query", json={"query": "show donor churn", "limit": 5})
    assert rv.status_code == 200
    payload = rv.get_json()
    assert payload["query"] == "show donor churn"
    assert isinstance(payload["sections"], list)
    assert payload["sections"][0]["title"] == "Predictive donor churn and lifetime value"


def test_minion_chat_logs_approval_token_issue_verify_and_replay_reject(client, app, monkeypatch):
    _ensure_user(app, "minion_admin_token3", "minion_admin_token3@test.local", "admin", "admin_token3_pass_123")
    _login(client, "minion_admin_token3", "admin_token3_pass_123")

    captured: list[tuple[str, dict | None]] = []

    def _capture_log_event(_db_path, actor, action, entity, metadata):
        captured.append((action, metadata))

    monkeypatch.setattr("ngo_homesuite.web.ai_routes.log_event", _capture_log_event)
    monkeypatch.setattr("ngo_homesuite.web.ai_routes.HomeSuiteMinion.from_app", lambda: _FakeMinionPending())

    issue_rv = client.post(
        "/ai/minion/chat",
        json={"prompt": "Create donor Jane", "allow_actions": True},
    )
    assert issue_rv.status_code == 200
    issue_data = issue_rv.get_json()
    token = issue_data["actions"][0]["approval_token"]

    monkeypatch.setattr("ngo_homesuite.web.ai_routes.HomeSuiteMinion.from_app", lambda: _FakeMinion())
    verify_rv = client.post(
        "/ai/minion/chat",
        json={
            "prompt": "Create donor Jane",
            "allow_actions": True,
            "approved_actions": [{"tool": "create_donor", "token": token}],
            "tool_allowlist": ["create_donor"],
        },
    )
    assert verify_rv.status_code == 200
    verify_passed = _FakeMinion.last_kwargs
    assert verify_passed is not None
    assert verify_passed["runtime_ctx"]["approved_actions"] == ["create_donor"]

    replay_rv = client.post(
        "/ai/minion/chat",
        json={
            "prompt": "Create donor Jane",
            "allow_actions": True,
            "approved_actions": [{"tool": "create_donor", "token": token}],
            "tool_allowlist": ["create_donor"],
        },
    )
    assert replay_rv.status_code == 200
    replay_passed = _FakeMinion.last_kwargs
    assert replay_passed is not None
    assert replay_passed["runtime_ctx"]["approved_actions"] == []

    token_actions = [action for action, _ in captured if action.startswith("minion_approval_token_")]
    assert "minion_approval_token_issued" in token_actions
    assert "minion_approval_token_verified" in token_actions
    assert "minion_approval_token_rejected" in token_actions

    rejected_metadata = [metadata for action, metadata in captured if action == "minion_approval_token_rejected"]
    assert any((md or {}).get("reason") == "replay" for md in rejected_metadata)


def test_minion_chat_rejects_cross_tenant_payload(client, app, monkeypatch):
    _ensure_user(app, "minion_tenant_staff", "minion_tenant_staff@test.local", "staff", "tenant_staff_pass_123")
    _login(client, "minion_tenant_staff", "tenant_staff_pass_123")

    monkeypatch.setattr("ngo_homesuite.web.ai_routes.HomeSuiteMinion.from_app", lambda: _FakeMinion())

    rv = client.post(
        "/ai/minion/chat",
        json={
            "prompt": "summarize donor trend",
            "tenant_id": "other-tenant",
            "context": {"active_page": "donors"},
        },
    )
    assert rv.status_code == 403
    assert "tenant_id" in rv.get_json()["error"]


def test_minion_chat_rate_limited(client, app, monkeypatch):
    _ensure_user(app, "minion_rate_user", "minion_rate_user@test.local", "staff", "rate_user_pass_123")
    _login(client, "minion_rate_user", "rate_user_pass_123")

    monkeypatch.setattr("ngo_homesuite.web.ai_routes.HomeSuiteMinion.from_app", lambda: _FakeMinion())

    old_rate = app.config.get("MINION_RATE_LIMIT_PER_MIN")
    old_enabled = app.config.get("RATELIMIT_ENABLED")
    try:
        app.config["MINION_RATE_LIMIT_PER_MIN"] = 1
        app.config["RATELIMIT_ENABLED"] = True

        rv1 = client.post("/ai/minion/chat", json={"prompt": "First request"})
        assert rv1.status_code == 200

        rv2 = client.post("/ai/minion/chat", json={"prompt": "Second request"})
        assert rv2.status_code == 429
        body = rv2.get_json()
        assert "rate limit" in body["error"].lower()
        assert int(body["retry_after_sec"]) >= 1
    finally:
        app.config["MINION_RATE_LIMIT_PER_MIN"] = old_rate
        app.config["RATELIMIT_ENABLED"] = old_enabled


def test_minion_chat_reuses_session_conversation(client, app, monkeypatch):
    _ensure_user(app, "minion_session_user", "minion_session_user@test.local", "staff", "session_user_pass_123")
    _login(client, "minion_session_user", "session_user_pass_123")

    monkeypatch.setattr("ngo_homesuite.web.ai_routes.HomeSuiteMinion.from_app", lambda: _FakeMinion())

    rv1 = client.post("/ai/minion/chat", json={"prompt": "First helper question"})
    rv2 = client.post("/ai/minion/chat", json={"prompt": "Second helper question"})

    assert rv1.status_code == 200
    assert rv2.status_code == 200

    with app.app_context():
        user = User.query.filter_by(username="minion_session_user").first()
        assert user is not None
        conversations = AIConversation.query.filter_by(user_id=user.id).all()
        assert len(conversations) == 1
        messages = AIMessage.query.filter_by(conversation_id=conversations[0].id).all()
        assert len(messages) == 4


def test_minion_conversation_reset_rotates_session(client, app, monkeypatch):
    _ensure_user(app, "minion_reset_user", "minion_reset_user@test.local", "staff", "reset_user_pass_123")
    _login(client, "minion_reset_user", "reset_user_pass_123")

    monkeypatch.setattr("ngo_homesuite.web.ai_routes.HomeSuiteMinion.from_app", lambda: _FakeMinion())

    first_rv = client.post("/ai/minion/chat", json={"prompt": "Start a conversation"})
    assert first_rv.status_code == 200

    with client.session_transaction() as sess:
        first_session_id = sess.get("ngo_ai_session_id")

    reset_rv = client.post("/ai/conversation/reset")
    assert reset_rv.status_code == 200
    reset_data = reset_rv.get_json()
    assert reset_data["ok"] is True
    assert reset_data["session_id"] != first_session_id

    with client.session_transaction() as sess:
        assert sess.get("ngo_ai_session_id") == reset_data["session_id"]


def test_current_conversation_endpoint_returns_session_messages(client, app, monkeypatch):
    _ensure_user(app, "minion_current_user", "minion_current_user@test.local", "viewer", "current_user_pass_123")
    _login(client, "minion_current_user", "current_user_pass_123")

    monkeypatch.setattr("ngo_homesuite.web.ai_routes.HomeSuiteMinion.from_app", lambda: _FakeMinion())

    chat_rv = client.post("/ai/minion/chat", json={"prompt": "Show my current AI thread"})
    assert chat_rv.status_code == 200

    current_rv = client.get("/ai/conversation/current")
    assert current_rv.status_code == 200
    data = current_rv.get_json()
    assert isinstance(data.get("session_id"), str)
    assert isinstance(data.get("messages"), list)
    assert len(data["messages"]) == 2
    assert data["messages"][0]["role"] == "user"
    assert data["messages"][1]["role"] == "assistant"

