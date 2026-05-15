"""
healthcheck.py

Simple DB health-check API for liveness/readiness probes.
"""
import sys
import logging
from ngo_homesuite.db.connection import connect_db, run_db

def health_check():
    try:
        def check(conn, cur):
            cur.execute("SELECT 1")
            return cur.fetchone()[0] == 1
        ok = run_db(check, retries=1)
        if ok:
            print("DB_HEALTH: OK")
            sys.exit(0)
        else:
            print("DB_HEALTH: FAIL")
            sys.exit(2)
    except Exception as e:
        logging.exception("DB health check failed")
        print(f"DB_HEALTH: ERROR: {e}")
        sys.exit(2)

if __name__ == "__main__":
    health_check()
