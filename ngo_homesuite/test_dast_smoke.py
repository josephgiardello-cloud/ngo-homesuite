from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parent.parent / "tools" / "dast_smoke.py"
    spec = importlib.util.spec_from_file_location("dast_smoke", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)

    import sys

    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_dast_run_marks_open_redirect_blocked_without_external_location(monkeypatch):
    mod = _load_module()

    calls = {"n": 0}

    def fake_request(method, url, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return 200, {
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "SAMEORIGIN",
                "Content-Security-Policy": "default-src 'self'",
            }, ""
        if calls["n"] == 2:
            return 405, {}, ""
        if calls["n"] == 3:
            return 302, {"Location": "/auth/login"}, ""
        return 200, {}, ""

    monkeypatch.setattr(mod, "_http_request", fake_request)
    monkeypatch.setattr(mod, "_rand_user", lambda: "dast_unit_user")

    results = mod._run("http://example.test", timeout=1.0)
    by_name = {r.name: r for r in results}

    assert by_name["security_headers_root"].passed is True
    assert by_name["logout_get_blocked"].passed is True
    assert by_name["register_for_open_redirect_probe"].passed is True
    assert by_name["login_open_redirect_blocked"].passed is True


def test_dast_run_fails_when_external_redirect_present(monkeypatch):
    mod = _load_module()

    calls = {"n": 0}

    def fake_request(method, url, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return 200, {
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "SAMEORIGIN",
                "Content-Security-Policy": "default-src 'self'",
            }, ""
        if calls["n"] == 2:
            return 405, {}, ""
        if calls["n"] == 3:
            return 302, {"Location": "/auth/login"}, ""
        return 302, {"Location": "https://evil.example/phish"}, ""

    monkeypatch.setattr(mod, "_http_request", fake_request)
    monkeypatch.setattr(mod, "_rand_user", lambda: "dast_unit_user")

    results = mod._run("http://example.test", timeout=1.0)
    by_name = {r.name: r for r in results}
    assert by_name["login_open_redirect_blocked"].passed is False
