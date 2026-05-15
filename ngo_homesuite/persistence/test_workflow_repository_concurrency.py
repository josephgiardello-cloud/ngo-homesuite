from __future__ import annotations

import pytest

from ngo_homesuite.app_factory import create_app
from ngo_homesuite.flask_config import TestingConfig
from ngo_homesuite.models.core import Organization


@pytest.fixture(scope="module")
def app():
    return create_app(TestingConfig)


def test_workflow_repository_optimistic_lock_conflict(app):
    with app.app_context():
        container = app.extensions["v2_container"]
        org = Organization.query.filter_by(is_active=True).first()
        assert org is not None

        created = container.create_workflow_instance(org_id=str(org.id), workflow_type="case_intake")
        repository = container.workflow_repository

        stale_a = repository.get(created.instance_id, org_id=str(org.id))
        stale_b = repository.get(created.instance_id, org_id=str(org.id))
        assert stale_a is not None and stale_b is not None

        stale_a.current_step = "verification"
        stale_a.history.append({"event_type": "synthetic_a", "payload": {}, "from_step": "intake", "to_step": "verification"})
        saved_a = repository.save(stale_a)
        assert saved_a.version >= 2

        stale_b.current_step = "assignment"
        stale_b.history.append({"event_type": "synthetic_b", "payload": {}, "from_step": "intake", "to_step": "assignment"})

        with pytest.raises(RuntimeError, match="Optimistic lock conflict"):
            repository.save(stale_b)
