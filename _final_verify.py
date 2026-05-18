import os
from pathlib import Path
os.chdir(Path(__file__).resolve().parent)
from ngo_homesuite.app_factory import create_app
from ngo_homesuite.flask_config import TestingConfig
from ngo_homesuite.models.core import db, Donor, Organization

class _TestCfg(TestingConfig):
    COPILOT_ENABLED = True

app = create_app(_TestCfg)
with app.app_context():
    print(f"Final verify - Orgs: {Organization.query.count()}")
    print(f"Final verify - Donors: {Donor.query.count()}")
