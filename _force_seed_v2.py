import os
os.chdir(r'C:\Users\josep\OneDrive\Desktop\Codes\ngo-homesuite')
from ngo_homesuite.app_factory import create_app
from ngo_homesuite.flask_config import TestingConfig
from ngo_homesuite.models.core import db, Donor, Organization, User

class _TestCfg(TestingConfig):
    COPILOT_ENABLED = True

app = create_app(_TestCfg)
with app.app_context():
    db.create_all()
    from sqlalchemy import text
    tables = db.session.execute(text("SELECT name FROM sqlite_master WHERE type='table';")).fetchall()
    print("Tables in DB:", [t[0] for t in tables])
    
    # Try cleaning again now that tables should exist
    for table in ["donations", "expenses", "volunteers", "donors", "projects", "funds", "users", "organizations"]:
        try:
            db.session.execute(text(f"DELETE FROM {table}"))
        except Exception as e:
            print(f"Error cleaning {table}: {e}")
    db.session.commit()
    print("Database cleaned.")

    from ngo_homesuite.app_factory import seed_demo_data
    seed_demo_data(app)
    
    print(f"Orgs: {Organization.query.count()}")
    print(f"Donors: {Donor.query.count()}")
    for d in Donor.query.all():
        print(f" - Donor: {d.name}, Org ID: {d.organization_id}")
