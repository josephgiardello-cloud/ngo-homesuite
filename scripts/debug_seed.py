from __future__ import annotations

import logging

from ngo_homesuite.app_factory import create_app
from ngo_homesuite.flask_config import TestingConfig
from ngo_homesuite.models.core import Donor, Organization


class _TestCfg(TestingConfig):
    COPILOT_ENABLED = True


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    app = create_app(_TestCfg)
    with app.app_context():
        print("Orgs after create_app:", Organization.query.count())
        print("Donors after create_app:", Donor.query.count())


if __name__ == "__main__":
    main()
