import os
from pathlib import Path
os.chdir(Path(__file__).resolve().parent)
from ngo_homesuite.app_factory import create_app
from ngo_homesuite.flask_config import TestingConfig
from ngo_homesuite.models.core import db, Donor, Organization, User, Project, Fund, Volunteer, Expense
from sqlalchemy import inspect as sa_inspect

class _TestCfg(TestingConfig):
    COPILOT_ENABLED = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'

app = create_app(_TestCfg)
with app.app_context():
    from ngo_homesuite.app_factory import get_runtime_settings
    db.create_all()
    
    org = Organization(name='Test Org', slug='test-org', country='US', city='Austin', is_active=True)
    db.session.add(org)
    db.session.flush()
    print(f"Org ID: {org.id}")
    
    print(f"Donor count before: {Donor.query.filter_by(organization_id=org.id).count()}")
    
    # Simulate the Donor seeding block
    donors = [
        Donor(organization_id=org.id, name='Ana Martins', email='ana@example.org', phone='+1-555-0101', donor_type='individual'),
        Donor(organization_id=org.id, name='Bright Future Foundation', email='contact@brightfuture.org', donor_type='foundation'),
    ]
    db.session.add_all(donors)
    db.session.flush()
    
    print(f"Donor count after add/flush: {Donor.query.filter_by(organization_id=org.id).count()}")
    
    db.session.commit()
    print(f"Donor count after commit: {Donor.query.filter_by(organization_id=org.id).count()}")
