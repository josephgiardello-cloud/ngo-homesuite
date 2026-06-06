import os, sys, traceback
from pathlib import Path
os.chdir(Path(__file__).resolve().parent)

import ngo_homesuite.app_factory as af
original_seed = af.seed_demo_data

def patched_seed(app):
    print("[TRACE] seed_demo_data START")
    try:
        from ngo_homesuite.models.core import db, Donor
        # Check count before
        count_before = Donor.query.count()
        print(f"[TRACE] Donor count before original_seed: {count_before}")
        
        result = original_seed(app)
        
        count_after = Donor.query.count()
        print(f"[TRACE] Donor count after original_seed: {count_after}")
        return result
    except Exception as e:
        print(f"[TRACE] seed_demo_data EXCEPTION: {e}")
        traceback.print_exc()
        raise

af.seed_demo_data = patched_seed

from ngo_homesuite.flask_config import TestingConfig
class _TestCfg(TestingConfig):
    MINION_ENABLED = True
    ENABLE_DEMO_SEED = True

from ngo_homesuite.app_factory import create_app
print("[TRACE] Calling create_app")
app = create_app(_TestCfg)
print("[TRACE] create_app FINISHED")

from ngo_homesuite.models.core import Donor
with app.app_context():
    print(f"[FINAL] Donor count = {Donor.query.count()}")

