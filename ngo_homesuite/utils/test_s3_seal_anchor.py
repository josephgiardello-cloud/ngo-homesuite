
# Patch: make import work when run as a script

# Robust import: find workspace root and add to sys.path
import os
import sys
import datetime
import json
current = os.path.abspath(os.path.dirname(__file__))
while True:
    if os.path.exists(os.path.join(current, 'ngo_homesuite')):
        sys.path.insert(0, current)
        break
    parent = os.path.dirname(current)
    if parent == current:
        break
    current = parent
from ngo_homesuite.utils.integrity_drift import append_baseline_log

def trigger_seal_entries(log_path=None, entries=100, table_name="audit_log"):
    """
    Write enough baseline entries to trigger a seal and S3 anchoring.
    Set INTEGRITY_LOG_SEAL_INTERVAL=100 (default) or override as needed.
    """
    now = datetime.datetime.now(datetime.UTC).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
    for i in range(entries):
        append_baseline_log(
            table_name=table_name,
            table_hash=f"testhash{i}",
            schema_version="v1",
            created_at=now,
            hmac_sig=f"hmactest{i}",
            log_path=log_path
        )
    print(f"Inserted {entries} entries. Check S3 for anchored seal.")

if __name__ == "__main__":
    # You can set log_path to a temp file or leave as default
    log_path = os.environ.get("INTEGRITY_BASELINE_LOG")
    entries = int(os.environ.get("INTEGRITY_SEAL_TEST_ENTRIES", "100"))
    trigger_seal_entries(log_path=log_path, entries=entries)
