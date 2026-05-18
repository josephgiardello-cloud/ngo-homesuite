import os, sys, traceback
os.chdir(r'C:\Users\josep\OneDrive\Desktop\Codes\ngo-homesuite')

from ngo_homesuite.flask_config import TestingConfig
from ngo_homesuite.models.core import db, User, Organization, Donor, Donation, Project, Fund, Volunteer, Expense
from ngo_homesuite.config import get_runtime_settings
from sqlalchemy import inspect as sa_inspect
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.pool import StaticPool

class _TestCfg(TestingConfig):
    COPILOT_ENABLED = True

from ngo_homesuite.app_factory import create_app
app = create_app(_TestCfg)

# Now manually run seed_demo_data step by step with exception catching
with app.app_context():
    from ngo_homesuite.models.core import DonationReceipt
    
    org = Organization.query.filter_by(slug='community-hope-initiative').first()
    print(f"Org: {org}, id={getattr(org, 'id', None)}")
    
    if org:
        donors = Donor.query.filter_by(organization_id=org.id).order_by(Donor.id).all()
        print(f"Donors count: {len(donors)}")
    
        # Try to get columns
        try:
            cols = {c['name'] for c in sa_inspect(db.engine).get_columns('donations')}
            print(f"donation columns: {sorted(cols)}")
        except Exception as e:
            print(f"get_columns failed: {e}")
            traceback.print_exc()
        
        # Try adding a donor
        try:
            d = Donor(organization_id=org.id, name='Test', email='test@test.com', donor_type='individual')
            db.session.add(d)
            db.session.flush()
            print(f"Donor flushed with id={d.id}")
            db.session.commit()
            print(f"Donor committed!")
            print(f"Donors now: {Donor.query.count()}")
        except Exception as e:
            print(f"Donor add/commit failed: {e}")
            traceback.print_exc()
            db.session.rollback()
    else:
        print("Organization 'community-hope-initiative' not found.")
