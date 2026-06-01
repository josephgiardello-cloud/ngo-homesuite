from __future__ import annotations

import pytest

from ngo_homesuite.auth import models, session


def test_login_delegates_to_authenticate_user(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[str, str, str | None]] = []

    monkeypatch.setattr(session, "prompt_non_empty", lambda _prompt: "alice")
    monkeypatch.setattr(session.getpass, "getpass", lambda _prompt: "secret")

    def _fake_authenticate_user(username: str, password: str, ip_address: str | None = None) -> dict[str, object]:
        captured.append((username, password, ip_address))
        return {"id": 7, "username": username, "role": "admin"}

    monkeypatch.setattr(models, "authenticate_user", _fake_authenticate_user)

    assert session.login() == {"id": 7, "username": "alice", "role": "admin"}
    assert captured == [("alice", "secret", None)]


def test_login_retries_until_auth_error(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    sleep_calls: list[float] = []
    auth_calls: list[tuple[str, str]] = []

    monkeypatch.setattr(session, "MAX_LOGIN_ATTEMPTS", 3)
    monkeypatch.setattr(session, "LOGIN_BACKOFF_BASE_SECONDS", 0.5)
    monkeypatch.setattr(session, "prompt_non_empty", lambda _prompt: "alice")
    monkeypatch.setattr(session.getpass, "getpass", lambda _prompt: "wrong")
    monkeypatch.setattr(session.time, "sleep", lambda delay: sleep_calls.append(delay))

    def _fake_authenticate_user(username: str, password: str, ip_address: str | None = None) -> dict[str, object]:
        auth_calls.append((username, password))
        raise ValueError("Invalid username or password.")

    monkeypatch.setattr(models, "authenticate_user", _fake_authenticate_user)

    with pytest.raises(session.AuthError, match="Too many failed login attempts"):
        session.login()

    assert auth_calls == [("alice", "wrong"), ("alice", "wrong"), ("alice", "wrong")]
    assert sleep_calls == [0.5, 1.0, 2.0]
    assert capsys.readouterr().out.count("Invalid username or password.") == 3