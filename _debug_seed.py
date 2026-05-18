import os, sys
os.chdir(r'C:\Users\josep\OneDrive\Desktop\Codes\ngo-homesuite')
import logging
logging.basicConfig(level=logging.WARNING)

from ngo_homesuite.app_factory import create_app
from ngo_homesuite.flask_config import TestingConfig
from ngo_homesuite.models.core import Donor, Organization

class _TestCfg(TestingConfig):
    COPILOT_ENABLED = True

try:
    app = create_app(_TestCfg)
    with app.app_context():
        print("Orgs after create_app:", Organization.query.count())
        print("Donors after create_app:", Donor.query.count())
except Exception as e:
    import traceback
    traceback.print_exc()
    print("ERROR:", e)
