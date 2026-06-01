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
    org = Organization.query.filter_by(slug='community-hope-initiative').first()
    print("Org found:", org is not None)
    if org:
        print("Org ID:", org.id)
        donors_for_org = Donor.query.filter_by(organization_id=org.id).all()
        print("Donors count for org:", len(donors_for_org))
        all_donors = Donor.query.all()
        print("Total Donors in DB:", len(all_donors))
        
        # Manually try to add a donor and see if it works or fails
        try:
            d = Donor(organization_id=org.id, name='Test Donor', email='test@example.com', donor_type='individual')
            db.session.add(d)
            db.session.commit()
            print("Successfully added donor manually")
            print("Total Donors after manual add:", Donor.query.count())
        except Exception as e:
            print("Manual donor add error:", e)
            db.session.rollback()
