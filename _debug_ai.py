import os
os.chdir(r'C:\Users\josep\OneDrive\Desktop\Codes\ngo-homesuite')
from ngo_homesuite.app_factory import create_app
from ngo_homesuite.flask_config import TestingConfig

class _TestCfg(TestingConfig):
    COPILOT_ENABLED = True
    SQLALCHEMY_DATABASE_URI = 'sqlite://' # Force in-memory for testing if needed, or stick to default

app = create_app(_TestCfg)

from ngo_homesuite.ai.copilot_tools import CopilotToolRegistry
from ngo_homesuite.models.core import db, Donor, Organization

with app.app_context():
    # Ensure tables are created (app_factory already does seed, but let's be sure of the donor)
    org = Organization.query.first()
    if not org:
        org = Organization(name="Test Org", slug="test-org")
        db.session.add(org)
        db.session.commit()
    
    donor = Donor.query.get(1)
    if not donor:
        donor = Donor(id=1, name="Test Donor", organization_id=org.id, email="test@example.com")
        db.session.add(donor)
        db.session.commit()
        print(f"Manually created Donor 1 in Org {org.id}")

    registry = CopilotToolRegistry()
    print("Orgs:", Organization.query.count())
    print("Donors:", Donor.query.count())
    
    payload = registry.execute("donor_profile_insights", {"donor_id": 1}, {"organization_id": org.id, "actor": "test"})
    print("Payload:", payload)
