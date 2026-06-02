from __future__ import annotations

import re
from collections.abc import Iterable

import pytest

from ngo_homesuite.models.core import Organization, User, db


MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
PUBLIC_MUTATING_ROUTES = {
    "/auth/login",
    "/auth/register",
    "/auth/password/forgot",
    "/give",
    "/integrations/stripe/webhook",
    "/api/v2/forms/submissions/public",
    "/api/v2/campaigns/email/preferences",
}
SENSITIVE_PREFIXES = (
    "/admin",
    "/api/v2",
    "/volunteers",
    "/integrations/ops",
    "/integrations/accounting",
)


@pytest.fixture(scope="module")
def app(shared_test_app):
    return shared_test_app


@pytest.fixture()
def client(app):
    return app.test_client()


def _ensure_user(app, username: str, email: str, role: str, password: str) -> None:
    with app.app_context():
        org = Organization.query.filter_by(is_active=True).order_by(Organization.id.asc()).first()
        if org is None:
            org = Organization(name="RBAC Audit Org", slug="rbac-audit-org", is_active=True)
            db.session.add(org)
            db.session.commit()

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
        elif user.organization_id is None:
            user.organization_id = org.id
            db.session.commit()


def _login(client, username: str, password: str) -> None:
    client.post("/auth/login", data={"username": username, "password": password})


def _materialize_rule_path(rule_text: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        converter = match.group("converter") or "string"
        if converter == "int":
            return "1"
        if converter == "float":
            return "1.0"
        if converter == "path":
            return "sample/path"
        if converter == "uuid":
            return "00000000-0000-0000-0000-000000000000"
        return "sample"

    return re.sub(r"<(?:(?P<converter>[^:>]+):)?(?P<name>[^>]+)>", _replace, rule_text)


def _mutating_rules(app) -> Iterable[tuple[str, str, str, tuple[str, ...] | None]]:
    for rule in app.url_map.iter_rules():
        if rule.rule.startswith("/static"):
            continue
        methods = sorted((rule.methods or set()) & MUTATING_METHODS)
        if not methods:
            continue
        view_func = app.view_functions.get(rule.endpoint)
        required_roles = getattr(view_func, "required_roles", None) if view_func else None
        for method in methods:
            yield rule.rule, method, rule.endpoint, required_roles


def test_anonymous_user_cannot_successfully_call_mutating_routes(client, app):
    failures: list[str] = []
    for raw_path, method, _endpoint, _roles in _mutating_rules(app):
        materialized = _materialize_rule_path(raw_path)
        if materialized in PUBLIC_MUTATING_ROUTES:
            continue

        rv = client.open(materialized, method=method, json={}, follow_redirects=False)
        if 200 <= rv.status_code < 300:
            failures.append(f"{method} {materialized} -> {rv.status_code}")

    assert not failures, "Anonymous access unexpectedly succeeded: " + "; ".join(failures)


def test_sensitive_mutating_routes_define_explicit_rbac_roles(app):
    failures: list[str] = []
    for raw_path, method, endpoint, roles in _mutating_rules(app):
        if not raw_path.startswith(SENSITIVE_PREFIXES):
            continue
        if _materialize_rule_path(raw_path) in PUBLIC_MUTATING_ROUTES:
            continue
        if not roles:
            failures.append(f"{method} {raw_path} ({endpoint}) missing roles_required")

    assert not failures, "RBAC metadata missing: " + "; ".join(failures)


def test_viewer_is_denied_for_all_non_viewer_mutating_routes(client, app):
    _ensure_user(app, "rbac_viewer", "rbac_viewer@test.local", "viewer", "RbacViewer123!")
    _login(client, "rbac_viewer", "RbacViewer123!")

    failures: list[str] = []
    for raw_path, method, _endpoint, roles in _mutating_rules(app):
        if not roles or "viewer" in roles:
            continue

        materialized = _materialize_rule_path(raw_path)
        rv = client.open(materialized, method=method, json={}, follow_redirects=False)
        if rv.status_code != 403:
            failures.append(f"{method} {materialized} -> {rv.status_code}")

    assert not failures, "Viewer bypassed role gates: " + "; ".join(failures)
