import os
os.chdir(r'C:\Users\josep\OneDrive\Desktop\Codes\ngo-homesuite')
from ngo_homesuite.app_factory import create_app
from ngo_homesuite.flask_config import TestingConfig
from ngo_homesuite.models.core import db, Donor, Organization

class _TestCfg(TestingConfig):
    COPILOT_ENABLED = True

app = create_app(_TestCfg)
with app.app_context():
    from flask import current_app
    # Check what seed_demo_data actually does
    import inspect
    from ngo_homesuite.app_factory import seed_demo_data
    print("Seed source:", inspect.getsource(seed_demo_data))
