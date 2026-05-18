import os, sys, traceback
from pathlib import Path
os.chdir(Path(__file__).resolve().parent)

from ngo_homesuite.flask_config import TestingConfig
class _TestCfg(TestingConfig):
    COPILOT_ENABLED = True
    ENABLE_DEMO_SEED = True

from ngo_homesuite.models.core import db, Donor, Organization
from ngo_homesuite.app_factory import create_app

app = create_app(_TestCfg)

with app.app_context():
    print(f"Donor count: {Donor.query.count()}")
    org = Organization.query.filter_by(slug='community-hope-initiative').first()
    if org:
        print(f"Organization ID: {org.id}")
        count = Donor.query.filter_by(organization_id=org.id).count()
        print(f"Donor count for org {org.id}: {count}")
    else:
        print("Organization 'community-hope-initiative' NOT FOUND")
