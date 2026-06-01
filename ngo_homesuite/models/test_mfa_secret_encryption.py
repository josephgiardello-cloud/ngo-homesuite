from __future__ import annotations

import pytest

import ngo_homesuite.models.core as core


def test_ensure_mfa_secret_fails_closed_without_encryption_material(monkeypatch):
    user = core.User(username="mfa_user", email="mfa_user@test.local", password_hash="x")
    monkeypatch.setattr(core, "_mfa_fernet", lambda: None)

    with pytest.raises(RuntimeError, match="MFA secret encryption key is not configured"):
        user.ensure_mfa_secret()


def test_verify_mfa_code_fails_closed_without_encryption_material(monkeypatch):
    user = core.User(username="mfa_verify", email="mfa_verify@test.local", password_hash="x")
    user.mfa_totp_secret = "JBSWY3DPEHPK3PXP"
    monkeypatch.setattr(core, "_mfa_fernet", lambda: None)

    with pytest.raises(RuntimeError, match="MFA secret encryption key is not configured"):
        user.verify_mfa_code("123456")
