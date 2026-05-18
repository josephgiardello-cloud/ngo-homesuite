import os, sys
os.chdir(r'C:\Users\josep\OneDrive\Desktop\Codes\ngo-homesuite')

from ngo_homesuite.flask_config import TestingConfig
class _TestCfg(TestingConfig):
    COPILOT_ENABLED = True

from ngo_homesuite.app_factory import create_app
from ngo_homesuite.models.core import db, Donor

app = create_app(_TestCfg)

with app.app_context():
    print(f"SQLALCHEMY_DATABASE_URI: {app.config.get('SQLALCHEMY_DATABASE_URI')}")
    # Force a donor into the DB
    d = Donor(organization_id=1, name="Persistence Test")
    db.session.add(d)
    db.session.commit()
    print(f"Donor count immediately after commit: {Donor.query.count()}")

# New app instance/context to see if it persisted
app2 = create_app(_TestCfg)
with app2.app_context():
    print(f"Donor count in new app instance: {Donor.query.count()}")
