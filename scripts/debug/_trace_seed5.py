import os, sys, traceback
from pathlib import Path
os.chdir(Path(__file__).resolve().parent)

from ngo_homesuite.flask_config import TestingConfig
class _TestCfg(TestingConfig):
    MINION_ENABLED = True
    ENABLE_DEMO_SEED = True

from ngo_homesuite.models.core import db, Donor, Organization, User, Fund, Project, Volunteer, Expense
from sqlalchemy import inspect as sa_inspect
from ngo_homesuite.app_factory import create_app

print("--- STARTING TRACE ---")

def seed_demo_data_traced(app):
    print("[1] Inside seed_demo_data")
    org = Organization.query.filter_by(slug='community-hope-initiative').first()
    if org is None:
        print("[2] Creating Org")
        org = Organization(
            name='Community Hope Initiative',
            slug='community-hope-initiative',
            is_active=True,
        )
        db.session.add(org)
        db.session.flush()
    print(f"[3] Org ID: {org.id}")

    user = User.query.filter_by(username='admin').first()
    if user is None:
        print("[4] Creating User")
        user = User(username='admin')
        db.session.add(user)
    user.organization_id = org.id
    user.email = 'admin@ngohomesuite.local'
    user.set_password('admin123')
    
    print("[5] Committing Org/User")
    db.session.commit()
    print(f"[6] After commit, User Org ID: {User.query.filter_by(username='admin').first().organization_id}")

    donor_count = Donor.query.filter_by(organization_id=org.id).count()
    print(f"[7] Donor count for org {org.id}: {donor_count}")
    if donor_count == 0:
        print("[8] Adding Donors")
        donors = [
            Donor(organization_id=org.id, name='Ana Martins', email='ana@example.org', donor_type='individual'),
            Donor(organization_id=org.id, name='Foundation', email='f@example.org', donor_type='foundation'),
        ]
        db.session.add_all(donors)
        print("[9] Calling flush")
        db.session.flush()
        print(f"[10] After flush, Donor count: {Donor.query.filter_by(organization_id=org.id).count()}")

    print("[11] Final commit")
    db.session.commit()
    print(f"[12] After final commit, Donor count: {Donor.query.filter_by(organization_id=org.id).count()}")

# Patch it locally for this test
import ngo_homesuite.app_factory as af
af.seed_demo_data = seed_demo_data_traced

app = create_app(_TestCfg)
with app.app_context():
    print(f"[END] Final Donor count: {Donor.query.count()}")

