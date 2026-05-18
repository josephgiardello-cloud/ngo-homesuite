from __future__ import annotations

import logging
from pathlib import Path

from ngo_homesuite.app_factory import create_app
from ngo_homesuite.flask_config import TestingConfig
from ngo_homesuite.ai.copilot_tools import CopilotToolRegistry
from ngo_homesuite.models.core import db, Donor, Organization


class _TestCfg(TestingConfig):
    COPILOT_ENABLED = True
    SQLALCHEMY_DATABASE_URI = "sqlite://"


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    logging.basicConfig(level=logging.WARNING)
    app = create_app(_TestCfg)

    with app.app_context():
        org = Organization.query.first()
        if org is None:
            org = Organization(name="Test Org", slug="test-org")
            db.session.add(org)
            db.session.commit()

        donor = Donor.query.get(1)
        if donor is None:
            donor = Donor(id=1, name="Test Donor", organization_id=org.id, email="test@example.com")
            db.session.add(donor)
            db.session.commit()

        registry = CopilotToolRegistry()
        payload = registry.execute(
            "donor_profile_insights",
            {"donor_id": 1},
            {"organization_id": org.id, "actor": "debug"},
        )
        print(f"repo_root={repo_root}")
        print("Payload:", payload)


if __name__ == "__main__":
    main()
