from __future__ import annotations

from collections.abc import Iterable

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


def _ensure_user(app, username: str, role: str, password: str) -> None:
    with app.app_context():
        user = User.query.filter_by(username=username).first()
        if user is None:
            user = User(
                username=username,
                email=f"{username}@test.local",
                role=role,
                is_active=True,
            )
            user.set_password(password)
            db.session.add(user)
            db.session.commit()


def _login(client, username: str, password: str) -> None:
    client.post("/auth/login", data={"username": username, "password": password})


def _static_get_routes(app) -> Iterable[str]:
    for rule in app.url_map.iter_rules():
        if "GET" not in rule.methods:
            continue
        if "<" in rule.rule:
            continue
        if rule.rule.startswith("/static"):
            continue
        yield rule.rule


def test_anonymous_requests_are_blocked_on_protected_static_get_routes(client, app):
    public_routes = {
        "/",
        "/about",
        "/help",
        "/health",
        "/give",
        "/auth/login",
        "/auth/register",
    }

    failures: list[str] = []
    for route in sorted(set(_static_get_routes(app))):
        if route in public_routes:
            continue
        rv = client.get(route, follow_redirects=False)
        if rv.status_code == 200:
            failures.append(route)

    assert not failures, f"Anonymous access unexpectedly allowed for: {', '.join(failures)}"


@pytest.mark.parametrize("route", ["/admin/users", "/admin/org", "/admin/roles"])
def test_viewer_role_cannot_access_admin_routes(client, app, route):
    _ensure_user(app, "route_matrix_viewer", "viewer", "RouteMatrix123!")
    _login(client, "route_matrix_viewer", "RouteMatrix123!")

    rv = client.get(route, follow_redirects=False)
    assert rv.status_code == 403
