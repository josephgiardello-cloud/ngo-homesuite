from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import re

import pytest

from ngo_homesuite.app_factory import create_app
from ngo_homesuite.flask_config import TestingConfig
from ngo_homesuite.models.core import User, db


@dataclass(frozen=True)
class RoutePolicy:
    access: str


ROUTE_POLICY_MANIFEST: dict[str, RoutePolicy] = {
    "/": RoutePolicy(access="public"),
    "/about": RoutePolicy(access="public"),
    "/admin/compliance/audit": RoutePolicy(access="admin"),
    "/admin/compliance/drift": RoutePolicy(access="admin"),
    "/admin/compliance/grant-deadlines": RoutePolicy(access="admin"),
    "/admin/custom-fields/schema": RoutePolicy(access="admin"),
    "/admin/grants/budget-workbench": RoutePolicy(access="admin"),
    "/admin/org": RoutePolicy(access="admin"),
    "/admin/roles": RoutePolicy(access="admin"),
    "/admin/external-comms/audit": RoutePolicy(access="admin"),
    "/admin/tony/audit": RoutePolicy(access="admin"),
    "/admin/tony/recommendations": RoutePolicy(access="admin"),
    "/admin/users": RoutePolicy(access="admin"),
    "/ai/health": RoutePolicy(access="authenticated"),
    "/ai/history": RoutePolicy(access="authenticated"),
    "/activity": RoutePolicy(access="authenticated"),
    "/api/docs": RoutePolicy(access="authenticated"),
    "/api/domain/snapshot": RoutePolicy(access="authenticated"),
    "/api/openapi.yaml": RoutePolicy(access="authenticated"),
    "/api/semantic/context": RoutePolicy(access="authenticated"),
    "/api/swagger": RoutePolicy(access="authenticated"),
    "/api/v1/audit/events": RoutePolicy(access="authenticated"),
    "/api/v1/metrics": RoutePolicy(access="authenticated"),
    "/api/v1/workflows": RoutePolicy(access="authenticated"),
    "/api/v2/activity/global": RoutePolicy(access="authenticated"),
    "/api/v2/activity/insights": RoutePolicy(access="authenticated"),
    "/api/v2/campaigns": RoutePolicy(access="authenticated"),
    "/api/v2/campaigns/email/click": RoutePolicy(access="tokenized_public"),
    "/api/v2/campaigns/email/open-pixel": RoutePolicy(access="public"),
    "/api/v2/tasks/my": RoutePolicy(access="authenticated"),
        "/api/v2/tasks/reminders": RoutePolicy(access="authenticated"),
    "/api/v2/tasks/board": RoutePolicy(access="authenticated"),
    "/api/v2/tasks/reminder-candidates": RoutePolicy(access="authenticated"),
    "/api/v2/cases": RoutePolicy(access="authenticated"),
    "/api/v2/cases/impact-report": RoutePolicy(access="authenticated"),
    "/api/v2/engagement-scores/at-risk": RoutePolicy(access="authenticated"),
    "/api/v2/grants": RoutePolicy(access="authenticated"),
    "/api/v2/grants/calendar": RoutePolicy(access="authenticated"),
    "/api/v2/grants/pipeline-summary": RoutePolicy(access="authenticated"),
    "/api/v2/grants/restricted-funds": RoutePolicy(access="authenticated"),
    "/api/v2/membership/summary": RoutePolicy(access="authenticated"),
    "/api/v2/membership/tiers": RoutePolicy(access="authenticated"),
    "/api/v2/p2p/leaderboard": RoutePolicy(access="authenticated"),
    "/api/v2/p2p/pages": RoutePolicy(access="authenticated"),
    "/api/v2/smart-groups": RoutePolicy(access="authenticated"),
    "/api/v2/tasks": RoutePolicy(access="authenticated"),
    "/api/v2/tasks/overdue-summary": RoutePolicy(access="authenticated"),
    "/auth/login": RoutePolicy(access="public"),
    "/auth/mfa/setup": RoutePolicy(access="authenticated"),
    "/auth/register": RoutePolicy(access="public"),
    "/beneficiaries": RoutePolicy(access="authenticated"),
    "/beneficiaries/new": RoutePolicy(access="authenticated"),
    "/dashboard": RoutePolicy(access="authenticated"),
    "/donations": RoutePolicy(access="authenticated"),
    "/donations/new": RoutePolicy(access="authenticated"),
    "/donations/recurring": RoutePolicy(access="authenticated"),
    "/donors": RoutePolicy(access="authenticated"),
    "/donors/import": RoutePolicy(access="authenticated"),
    "/donors/dedupe": RoutePolicy(access="authenticated"),
    "/donors/new": RoutePolicy(access="authenticated"),
    "/expenses": RoutePolicy(access="authenticated"),
    "/expenses/new": RoutePolicy(access="authenticated"),
    "/funds": RoutePolicy(access="authenticated"),
    "/funds/new": RoutePolicy(access="authenticated"),
    "/give": RoutePolicy(access="public"),
    "/grants/": RoutePolicy(access="authenticated"),
    "/grants/pipeline": RoutePolicy(access="authenticated"),
    "/health": RoutePolicy(access="public"),
    "/health/live": RoutePolicy(access="probe"),
    "/health/ready": RoutePolicy(access="probe"),
    "/help": RoutePolicy(access="public"),
    "/integrations/accounting/sync/logs": RoutePolicy(access="authenticated"),
    "/integrations/email/queue": RoutePolicy(access="authenticated"),
    "/integrations/ops/jobs": RoutePolicy(access="authenticated"),
    "/integrations/ops/recent": RoutePolicy(access="authenticated"),
    "/integrations/ops/status": RoutePolicy(access="authenticated"),
    "/membership/summary": RoutePolicy(access="authenticated"),
    "/membership/tiers": RoutePolicy(access="authenticated"),
    "/metrics": RoutePolicy(access="public"),
    "/mobile/intake": RoutePolicy(access="authenticated"),
    "/p2p/leaderboard": RoutePolicy(access="authenticated"),
    "/p2p/manage": RoutePolicy(access="authenticated"),
    "/p2p/pages": RoutePolicy(access="authenticated"),
    "/programs/appointments": RoutePolicy(access="authenticated"),
    "/programs/cases": RoutePolicy(access="authenticated"),
    "/programs/impact": RoutePolicy(access="authenticated"),
    "/programs/intake/beneficiaries": RoutePolicy(access="authenticated"),
    "/projects": RoutePolicy(access="authenticated"),
    "/projects/new": RoutePolicy(access="authenticated"),
    "/public/donate": RoutePolicy(access="public"),
    "/public/donate/cancel": RoutePolicy(access="public"),
    "/public/donate/success": RoutePolicy(access="public"),
    "/campaigns/email-workbench": RoutePolicy(access="authenticated"),
    "/reports": RoutePolicy(access="authenticated"),
    "/reports/compliance/evidence": RoutePolicy(access="authenticated"),
    "/reports/funder": RoutePolicy(access="authenticated"),
    "/reports/scheduled": RoutePolicy(access="authenticated"),
    "/reports/trends/giving": RoutePolicy(access="authenticated"),
    "/reports/trends/retention": RoutePolicy(access="authenticated"),
    "/settings": RoutePolicy(access="authenticated"),
    "/setup": RoutePolicy(access="authenticated"),
    "/smart-groups/": RoutePolicy(access="authenticated"),
    "/tasks/board": RoutePolicy(access="authenticated"),
    "/tasks/": RoutePolicy(access="authenticated"),
    "/tasks/overdue": RoutePolicy(access="authenticated"),
    "/tony-scoring": RoutePolicy(access="authenticated"),
    "/volunteers": RoutePolicy(access="authenticated"),
    "/volunteers/hours": RoutePolicy(access="authenticated"),
    "/volunteers/shifts": RoutePolicy(access="authenticated"),
    "/volunteers/training/compliance": RoutePolicy(access="authenticated"),
    "/volunteers/training/courses": RoutePolicy(access="authenticated"),
    "/workflows": RoutePolicy(access="authenticated"),
}


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


def _logout(client) -> None:
    client.post("/auth/logout")


def _static_get_routes(app) -> Iterable[str]:
    for rule in app.url_map.iter_rules():
        if "GET" not in rule.methods:
            continue
        if "<" in rule.rule:
            continue
        if rule.rule.startswith("/static"):
            continue
        yield rule.rule


def _materialize_admin_rule_path(rule_text: str, target_user_id: int) -> str:
    def replace_match(match: re.Match[str]) -> str:
        param_name = match.group(1)
        if param_name == "user_id":
            return str(target_user_id)
        return "1"

    return re.sub(r"<(?:[^:>]+:)?([^>]+)>", replace_match, rule_text)


def test_static_get_route_policy_manifest_is_complete_and_exact(app):
    static_routes = set(_static_get_routes(app))
    manifest_routes = set(ROUTE_POLICY_MANIFEST)
    assert static_routes == manifest_routes


def test_anonymous_requests_follow_route_policy_manifest(client):
    failures: list[str] = []
    for route, policy in sorted(ROUTE_POLICY_MANIFEST.items()):
        rv = client.get(route, follow_redirects=False)
        if policy.access == "public":
            if rv.status_code != 200:
                failures.append(f"{route} expected 200, got {rv.status_code}")
            continue

        if policy.access == "tokenized_public":
            if rv.status_code not in (200, 400):
                failures.append(f"{route} expected 200/400 for tokenized public endpoint, got {rv.status_code}")
            continue

        if policy.access == "probe":
            # Probes are public (no redirect to login) but may return 200 or 503
            if rv.status_code not in (200, 503):
                failures.append(f"{route} expected 200/503 for probe, got {rv.status_code}")
            continue

        if rv.status_code == 200:
            failures.append(f"{route} expected non-200 for anonymous, got 200")

    assert not failures, "; ".join(failures)


def test_viewer_role_cannot_access_all_admin_routes_and_methods(client, app):
    _ensure_user(app, "route_matrix_viewer", "viewer", "RouteMatrix123!")
    _ensure_user(app, "route_matrix_target", "staff", "RouteMatrix123!")
    _login(client, "route_matrix_viewer", "RouteMatrix123!")

    with app.app_context():
        target_user = User.query.filter_by(username="route_matrix_target").first()
        assert target_user is not None
        target_user_id = int(target_user.id)

    failures: list[str] = []
    for rule in sorted(app.url_map.iter_rules(), key=lambda r: (r.rule, sorted(r.methods))):
        if not rule.rule.startswith("/admin"):
            continue
        path = _materialize_admin_rule_path(rule.rule, target_user_id)
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            request_payload = None
            if method in {"PATCH", "POST", "PUT", "DELETE"}:
                request_payload = {}
            rv = client.open(path, method=method, json=request_payload, follow_redirects=False)
            if rv.status_code != 403:
                failures.append(f"{method} {path} -> {rv.status_code}")

    assert not failures, f"Viewer accessed admin route(s): {', '.join(failures)}"
    _logout(client)
