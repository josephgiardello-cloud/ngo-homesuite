from __future__ import annotations

from ngo_homesuite.utils import integrity_drift


def test_get_allowed_tables_uses_default_set(monkeypatch):
    monkeypatch.delenv("INTEGRITY_ALLOWED_TABLES", raising=False)

    allowed = integrity_drift.get_allowed_tables()

    assert "audit_log" in allowed
    assert "workflow_events_v2" in allowed
    assert "donations" in allowed


def test_get_allowed_tables_honors_env_override(monkeypatch):
    monkeypatch.setenv("INTEGRITY_ALLOWED_TABLES", "custom_table_a, custom_table_b")

    allowed = integrity_drift.get_allowed_tables()

    assert allowed == {"custom_table_a", "custom_table_b"}
