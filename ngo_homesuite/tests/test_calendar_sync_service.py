from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ngo_homesuite.models.core import Organization, Task, db
from ngo_homesuite.services.calendar_sync_service import InMemoryCalendarProvider, sync_task_deadlines


@pytest.fixture(scope="module")
def app(shared_test_app):
    return shared_test_app


@pytest.fixture()
def ctx(app):
    with app.app_context():
        yield



def _mk_org(name: str, slug: str) -> Organization:
    org = Organization(name=name, slug=slug, is_active=True)
    db.session.add(org)
    db.session.flush()
    return org



def test_sync_task_deadlines_scopes_to_organization(ctx):
    org_a = _mk_org("Calendar Org A", "calendar-org-a")
    org_b = _mk_org("Calendar Org B", "calendar-org-b")

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    t1 = Task(
        organization_id=org_a.id,
        title="A task due",
        status="open",
        priority="high",
        due_date=now + timedelta(days=2),
    )
    t2 = Task(
        organization_id=org_a.id,
        title="A task done",
        status="done",
        priority="low",
        due_date=now + timedelta(days=1),
    )
    t3 = Task(
        organization_id=org_b.id,
        title="B task due",
        status="open",
        priority="medium",
        due_date=now + timedelta(days=3),
    )
    db.session.add_all([t1, t2, t3])
    db.session.commit()

    provider = InMemoryCalendarProvider()
    result = sync_task_deadlines(org_a.id, provider)

    assert result["synced"] == 1
    assert result["skipped"] == 0
    assert f"task:{t1.id}" in provider.events
    assert f"task:{t2.id}" not in provider.events
    assert f"task:{t3.id}" not in provider.events



def test_sync_task_deadlines_handles_no_due_dates(ctx):
    org = _mk_org("Calendar Org C", "calendar-org-c")

    task = Task(
        organization_id=org.id,
        title="No due date",
        status="open",
        priority="medium",
        due_date=None,
    )
    db.session.add(task)
    db.session.commit()

    provider = InMemoryCalendarProvider()
    result = sync_task_deadlines(org.id, provider)

    assert result["synced"] == 0
    assert provider.events == {}
