"""
cron_safe_rotate_db_key.py

A cron-safe wrapper for database key rotation. Ensures single-process execution, logs all actions, and is safe for use in scheduled jobs (e.g., cron, Task Scheduler).
"""
import os
import sys
import logging
import fcntl
from pathlib import Path
from ngo_homesuite.db.connection import rotate_db_key, DB_PATH, DB_ENCRYPTION_KEY_ENV

LOCKFILE = os.environ.get("NGO_HOMESUITE_ROTATE_LOCKFILE", f"{DB_PATH}.rotate.lock")
LOGFILE = os.environ.get("NGO_HOMESUITE_ROTATE_LOGFILE", f"{DB_PATH}.rotate.log")

logging.basicConfig(
    filename=LOGFILE,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

def main():
    with open(LOCKFILE, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logging.error("Another key rotation process is already running. Exiting.")
            sys.exit(1)
        try:
            old_key = os.environ.get("NGO_HOMESUITE_OLD_KEY")
            new_key = os.environ.get("NGO_HOMESUITE_NEW_KEY")
            rotate_db_key(old_key=old_key, new_key=new_key)
            logging.info("Key rotation completed successfully.")
        except Exception as e:
            logging.exception(f"Key rotation failed: {e}")
            sys.exit(2)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)

if __name__ == "__main__":
    main()
