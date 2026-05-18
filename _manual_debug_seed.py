import os
os.chdir(r'C:\Users\josep\OneDrive\Desktop\Codes\ngo-homesuite')
from ngo_homesuite.app_factory import create_app
from ngo_homesuite.flask_config import TestingConfig
from ngo_homesuite.models.core import db, Donor, Organization, User, Project, Fund, Volunteer, Expense
from sqlalchemy import text, inspect as sa_inspect
from ngo_homesuite.app_factory import get_runtime_settings

def manual_seed(app):
    with app.app_context():
        # Ensure Organization
        org = Organization.query.filter_by(slug='community-hope-initiative').first()
        if not org:
            org = Organization(
                name='Community Hope Initiative',
                slug='community-hope-initiative',
                description='Demo organization',
                country='US', city='Austin', is_active=True
            )
            db.session.add(org)
            db.session.flush()
        
        # Check Donor block specifically
        print(f"Donor count for org {org.id}: {Donor.query.filter_by(organization_id=org.id).count()}")
        
        donors = [
            Donor(organization_id=org.id, name='Ana Martins', email='ana@example.org', phone='+1-555-0101', donor_type='individual'),
            Donor(organization_id=org.id, name='Bright Future Foundation', email='contact@brightfuture.org', donor_type='foundation'),
        ]
        print(f"Adding {len(donors)} donors...")
        db.session.add_all(donors)
        db.session.flush() # Should assign IDs
        print(f"Post-flush donor count: {Donor.query.filter_by(organization_id=org.id).count()}")
        for d in donors:
            print(f" - ID: {d.id}, Name: {d.name}, Org: {d.organization_id}")
            
        db.session.commit()
        print(f"Post-commit donor count: {Donor.query.filter_by(organization_id=org.id).count()}")

class _TestCfg(TestingConfig):
    COPILOT_ENABLED = False # Disable auto-seed during creation

app = create_app(_TestCfg)
manual_seed(app)
