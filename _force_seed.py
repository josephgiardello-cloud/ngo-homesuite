import os
from pathlib import Path
os.chdir(Path(__file__).resolve().parent)
from ngo_homesuite.app_factory import create_app
from ngo_homesuite.flask_config import TestingConfig
from ngo_homesuite.models.core import db, Donor, Organization, User

class _TestCfg(TestingConfig):
    COPILOT_ENABLED = True
    # Do NOT use in-memory, use the real DB from TestingConfig or default

app = create_app(_TestCfg)
with app.app_context():
    # 1. Clean up everything to force a clean seed
    from sqlalchemy import text
    db.session.execute(text("DELETE FROM donor"))
    db.session.execute(text("DELETE FROM donation"))
    db.session.execute(text("DELETE FROM expense"))
    db.session.execute(text("DELETE FROM volunteer"))
    db.session.execute(text("DELETE FROM project"))
    db.session.execute(text("DELETE FROM fund"))
    db.session.execute(text("DELETE FROM user"))
    db.session.execute(text("DELETE FROM organization"))
    db.session.commit()
    print("Cleaned database.")

    # 2. Run seed_demo_data manually
    from ngo_homesuite.app_factory import seed_demo_data
    seed_demo_data(app)
    print("Ran seed_demo_data.")

    # 3. Verify counts
    print(f"Orgs: {Organization.query.count()}")
    print(f"Users: {User.query.count()}")
    print(f"Donors: {Donor.query.count()}")
