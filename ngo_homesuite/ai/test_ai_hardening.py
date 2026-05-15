"""Tests for AI hardening: RBAC gates, PII redaction, and conversation persistence."""
from __future__ import annotations

import json
import uuid
import pytest

# ---------------------------------------------------------------------------
# PII Redaction tests (no Flask context needed)
# ---------------------------------------------------------------------------

from ngo_homesuite.ai.pii_redact import redact_pii


class TestPiiRedaction:
    def test_email_redacted(self):
        text = "Please contact donor@example.com for follow-up."
        result, n = redact_pii(text)
        assert "[REDACTED_EMAIL]" in result
        assert "donor@example.com" not in result
        assert n >= 1

    def test_phone_redacted(self):
        text = "Call me at (555) 867-5309 tomorrow."
        result, n = redact_pii(text)
        assert "[REDACTED_PHONE]" in result
        assert n >= 1

    def test_ssn_redacted(self):
        text = "SSN: 123-45-6789"
        result, n = redact_pii(text)
        assert "[REDACTED_SSN]" in result
        assert "123-45-6789" not in result
        assert n >= 1

    def test_credit_card_redacted(self):
        text = "Card: 4111 1111 1111 1111"
        result, n = redact_pii(text)
        assert "[REDACTED_CC]" in result
        assert n >= 1

    def test_no_pii_unchanged(self):
        text = "How many donations were received last quarter?"
        result, n = redact_pii(text)
        assert result == text
        assert n == 0

    def test_multiple_pii_types(self):
        text = "Email john@test.org or call 555-123-4567"
        result, n = redact_pii(text)
        assert "[REDACTED_EMAIL]" in result
        assert "[REDACTED_PHONE]" in result
        assert n >= 2

    def test_empty_string(self):
        result, n = redact_pii("")
        assert result == ""
        assert n == 0

    def test_returns_tuple(self):
        out = redact_pii("hello")
        assert isinstance(out, tuple)
        assert len(out) == 2


# ---------------------------------------------------------------------------
# Flask-based tests (RBAC + persistence)
# ---------------------------------------------------------------------------

from ngo_homesuite.app_factory import create_app
from ngo_homesuite.models.core import db as _db, User, Organization, AIConversation, AIMessage


@pytest.fixture(scope="module")
def app():
    """Create a test Flask app with an in-memory SQLite DB."""
    from ngo_homesuite.flask_config import TestingConfig

    class _TestCfg(TestingConfig):
        APEX_AI_ENABLED = True
        APEX_BASE_URL = "http://localhost:11434"
        APEX_API_TOKEN = None
        APEX_MODEL = "llama3.2"
        APEX_TENANT_ID = "ngo-test"

    flask_app = create_app(_TestCfg)
    return flask_app


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def admin_user(app):
    with app.app_context():
        user = User.query.filter_by(username="test_admin").first()
        if user is None:
            user = User(
                username="test_admin",
                email="admin@test.local",
                role="admin",
                is_active=True,
            )
            user.set_password("admin_pass_123")
            _db.session.add(user)
            _db.session.commit()
        return user.id


@pytest.fixture()
def viewer_user(app):
    with app.app_context():
        user = User.query.filter_by(username="test_viewer").first()
        if user is None:
            user = User(
                username="test_viewer",
                email="viewer@test.local",
                role="viewer",
                is_active=True,
            )
            user.set_password("viewer_pass_123")
            _db.session.add(user)
            _db.session.commit()
        return user.id


def _login(client, username: str, password: str) -> None:
    client.post("/auth/login", data={"username": username, "password": password})


# ---------------------------------------------------------------------------
# RBAC tests
# ---------------------------------------------------------------------------

class TestRbacGates:
    def test_unauthenticated_chat_redirected(self, client):
        rv = client.post("/ai/chat", json={"prompt": "hello"})
        # Should redirect to login or 403, not 200
        assert rv.status_code in (302, 401, 403)

    def test_viewer_cannot_access_chat(self, client, app, viewer_user):
        _login(client, "test_viewer", "viewer_pass_123")
        rv = client.post("/ai/chat", json={"prompt": "hello"},
                         headers={"Accept": "application/json"})
        assert rv.status_code == 403

    def test_viewer_cannot_access_stream(self, client, app, viewer_user):
        _login(client, "test_viewer", "viewer_pass_123")
        rv = client.post("/ai/stream", json={"prompt": "hello"},
                         headers={"Accept": "application/json"})
        assert rv.status_code == 403

    def test_health_accessible_to_any_authenticated(self, client, viewer_user):
        _login(client, "test_viewer", "viewer_pass_123")
        rv = client.get("/ai/health")
        # 200 OK — health is login_required only, not role-gated
        assert rv.status_code == 200

    def test_admin_can_access_chat_endpoint(self, client, app, admin_user, monkeypatch):
        """Admin role should pass RBAC; we mock Apex to avoid network calls."""
        from unittest.mock import patch
        _login(client, "test_admin", "admin_pass_123")
        with patch("ngo_homesuite.web.ai_routes._client") as mock_client:
            mock_client.return_value.query.return_value = "Test answer"
            rv = client.post("/ai/chat", json={"prompt": "What is 2+2?"})
        # APEX_AI_ENABLED is True, admin, prompt provided → should get a 200
        assert rv.status_code == 200
        data = rv.get_json()
        assert "response" in data


# ---------------------------------------------------------------------------
# Conversation persistence tests
# ---------------------------------------------------------------------------

class TestConversationPersistence:
    def test_chat_persists_conversation(self, client, app, admin_user):
        from unittest.mock import patch
        _login(client, "test_admin", "admin_pass_123")
        with patch("ngo_homesuite.web.ai_routes._client") as mock_client:
            mock_client.return_value.query.return_value = "Four"
            rv = client.post("/ai/chat", json={"prompt": "What is 2+2?"})
        assert rv.status_code == 200

        with app.app_context():
            convs = AIConversation.query.filter_by(user_id=admin_user).all()
            assert len(convs) >= 1
            msgs = AIMessage.query.filter_by(conversation_id=convs[-1].id).all()
            roles = [m.role for m in msgs]
            assert "user" in roles
            assert "assistant" in roles

    def test_user_message_contains_prompt_hash(self, client, app, admin_user):
        import hashlib
        from unittest.mock import patch
        _login(client, "test_admin", "admin_pass_123")
        prompt_text = "Tell me about our biggest donors"
        with patch("ngo_homesuite.web.ai_routes._client") as mock_client:
            mock_client.return_value.query.return_value = "Here are your top donors."
            client.post("/ai/chat", json={"prompt": prompt_text})

        with app.app_context():
            convs = AIConversation.query.filter_by(user_id=admin_user).order_by(
                AIConversation.created_at.desc()
            ).all()
            user_msg = AIMessage.query.filter_by(
                conversation_id=convs[0].id, role="user"
            ).first()
            assert user_msg is not None
            assert user_msg.prompt_sha256 is not None
            # Hash should match the (possibly redacted) prompt
            expected = hashlib.sha256(user_msg.content.encode()).hexdigest()
            assert user_msg.prompt_sha256 == expected

    def test_history_endpoint_returns_conversations(self, client, app, admin_user):
        _login(client, "test_admin", "admin_pass_123")
        rv = client.get("/ai/history")
        assert rv.status_code == 200
        data = rv.get_json()
        assert isinstance(data, list)

    def test_history_denied_to_viewer(self, client, viewer_user):
        _login(client, "test_viewer", "viewer_pass_123")
        rv = client.get("/ai/history", headers={"Accept": "application/json"})
        assert rv.status_code == 403

    def test_history_scoped_to_user_organization(self, client, app):
        with app.app_context():
            org1 = Organization(name="AI Org One", slug="ai-org-one", is_active=True)
            org2 = Organization(name="AI Org Two", slug="ai-org-two", is_active=True)
            _db.session.add_all([org1, org2])
            _db.session.flush()

            user = User.query.filter_by(username="ai_org_admin").first()
            if user is None:
                user = User(
                    username="ai_org_admin",
                    email="ai.org.admin@test.local",
                    role="admin",
                    is_active=True,
                    organization_id=org1.id,
                )
                user.set_password("ai_org_admin_pass")
                _db.session.add(user)
                _db.session.flush()
            else:
                user.organization_id = org1.id

            conv_good = AIConversation(
                session_id=f"history-same-org-{uuid.uuid4().hex[:10]}",
                user_id=user.id,
                organization_id=org1.id,
                model="llama3.2",
            )
            conv_other = AIConversation(
                session_id=f"history-other-org-{uuid.uuid4().hex[:10]}",
                user_id=user.id,
                organization_id=org2.id,
                model="llama3.2",
            )
            _db.session.add_all([conv_good, conv_other])
            _db.session.commit()

        _login(client, "ai_org_admin", "ai_org_admin_pass")
        rv = client.get("/ai/history")
        assert rv.status_code == 200
        data = rv.get_json()
        session_ids = {item["session_id"] for item in data}
        assert any(s.startswith("history-same-org-") for s in session_ids)
        assert not any(s.startswith("history-other-org-") for s in session_ids)


# ---------------------------------------------------------------------------
# PII redaction is applied before sending to Apex
# ---------------------------------------------------------------------------

class TestPiiRedactionInRoutes:
    def test_email_in_prompt_is_redacted_before_apex(self, client, app, admin_user):
        from unittest.mock import patch, call
        _login(client, "test_admin", "admin_pass_123")
        raw_prompt = "Contact donor@secret.org about their pledge."
        with patch("ngo_homesuite.web.ai_routes._client") as mock_client:
            mock_client.return_value.query.return_value = "Done."
            client.post("/ai/chat", json={"prompt": raw_prompt})
            # Inspect what was actually passed to Apex
            call_args = mock_client.return_value.query.call_args
            sent_prompt = call_args[1]["prompt"] if call_args[1] else call_args[0][0]
            assert "donor@secret.org" not in sent_prompt
            assert "[REDACTED_EMAIL]" in sent_prompt
