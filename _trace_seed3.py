import os, sys, traceback
os.chdir(r'C:\Users\josep\OneDrive\Desktop\Codes\ngo-homesuite')

# Patch seed_demo_data to trace it
import ngo_homesuite.app_factory as af
original_seed = af.seed_demo_data

def patched_seed(app):
    try:
        original_seed(app)
        print("[SEED] seed_demo_data completed without exception")
    except Exception as e:
        print(f"[SEED] seed_demo_data RAISED: {type(e).__name__}: {e}")
        traceback.print_exc()
        raise

af.seed_demo_data = patched_seed

# Also patch db.session.commit to trace it
from ngo_homesuite.models.core import db
original_commit = db.session.commit

call_count = [0]
def traced_commit():
    call_count[0] += 1
    n = call_count[0]
    try:
        original_commit()
        # Query donors inside the same session state
        print(f"[COMMIT #{n}] success")
    except Exception as e:
        print(f"[COMMIT #{n}] RAISED: {type(e).__name__}: {e}")
        raise

# Also patch rollback
original_rollback = db.session.rollback
rollback_count = [0]
def traced_rollback():
    rollback_count[0] += 1
    print(f"[ROLLBACK #{rollback_count[0]}] called from:")
    traceback.print_stack(limit=5)
    original_rollback()

db.session.rollback = traced_rollback
db.session.commit = traced_commit

from ngo_homesuite.flask_config import TestingConfig
class _TestCfg(TestingConfig):
    COPILOT_ENABLED = True

from ngo_homesuite.app_factory import create_app
app = create_app(_TestCfg)

from ngo_homesuite.models.core import Donor
with app.app_context():
    print(f"[FINAL] Donor count = {Donor.query.count()}")
