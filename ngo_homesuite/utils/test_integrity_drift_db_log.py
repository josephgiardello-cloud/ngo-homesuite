import os
import tempfile
import json
import shutil
import datetime
import sqlite3
import pytest
from ngo_homesuite.utils import integrity_drift

def test_db_backed_append_only_log_basic():
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test_integrity_baseline_log.db")
    os.environ["INTEGRITY_BASELINE_LOG_MODE"] = "db"
    os.environ["INTEGRITY_BASELINE_LOG_DB"] = db_path
    try:
        # Write 3 entries with unique timestamps
        for i in range(3):
            now = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=i)).isoformat().replace('+00:00', 'Z')
            integrity_drift.append_baseline_log(
                table_name="audit_log",
                table_hash=f"hash{i}",
                schema_version="v1",
                created_at=now,
                hmac_sig=f"hmac{i}",
                db_path=db_path
            )
        # Check DB contents
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM baseline_log")
        count = cur.fetchone()[0]
        assert count >= 3
        # Check hash chaining
        cur.execute("SELECT seq, entry_hash, prev_hash FROM baseline_log ORDER BY seq")
        rows = cur.fetchall()
        prev_hash = "0" * 64
        for seq, entry_hash, prev in rows:
            assert prev == prev_hash
            prev_hash = entry_hash
        conn.close()
    finally:
        shutil.rmtree(tmpdir)
        os.environ.pop("INTEGRITY_BASELINE_LOG_MODE", None)
        os.environ.pop("INTEGRITY_BASELINE_LOG_DB", None)

def test_db_backed_append_only_log_seal(monkeypatch):
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test_integrity_baseline_log.db")
    os.environ["INTEGRITY_BASELINE_LOG_MODE"] = "db"
    os.environ["INTEGRITY_BASELINE_LOG_DB"] = db_path
    os.environ["INTEGRITY_LOG_SEAL_INTERVAL"] = "2"
    try:
        called = {}
        def dummy_anchor(seal):
            called['seal'] = seal
        monkeypatch.setattr(integrity_drift, 'anchor_seal_hook', dummy_anchor)
        for i in range(2):
            now = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=i)).isoformat().replace('+00:00', 'Z')
            integrity_drift.append_baseline_log(
                table_name="audit_log",
                table_hash=f"hash{i}",
                schema_version="v1",
                created_at=now,
                hmac_sig=f"hmac{i}",
                db_path=db_path
            )
        # Should have triggered a seal
        assert 'seal' in called and called['seal']['type'] == 'seal'
    finally:
        shutil.rmtree(tmpdir)
        os.environ.pop("INTEGRITY_BASELINE_LOG_MODE", None)
        os.environ.pop("INTEGRITY_BASELINE_LOG_DB", None)
        os.environ.pop("INTEGRITY_LOG_SEAL_INTERVAL", None)

def test_db_backed_append_only_log_truncation():

    # --- Drift detection tests (DB context, for completeness) ---
    def test_db_drift_detection_sustained_increasing_trend():
        counts = [10, 12, 14, 16, 18, 20, 22]
        drift, stats = integrity_drift.detect_3sigma_drift(counts, min_days=3)
        assert drift and stats[-2] == 'increasing' and 'trend' in (stats[-1] or ''), f"Expected increasing trend, got {stats}"

    def test_db_drift_detection_sustained_decreasing_trend():
        counts = [22, 20, 18, 16, 14, 12, 10]
        drift, stats = integrity_drift.detect_3sigma_drift(counts, min_days=3)
        assert drift and stats[-2] == 'decreasing' and 'trend' in (stats[-1] or ''), f"Expected decreasing trend, got {stats}"

    def test_db_drift_detection_no_drift():
        counts = [10, 11, 9, 10, 11, 10, 9]
        drift, stats = integrity_drift.detect_3sigma_drift(counts, min_days=3)
        assert not drift, f"Expected no drift, got {stats}"

    def test_db_drift_detection_short_window():
        counts = [10, 12]
        drift, stats = integrity_drift.detect_3sigma_drift(counts, min_days=3)
        assert not drift, f"Expected no drift for short window, got {stats}"
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test_integrity_baseline_log.db")
    os.environ["INTEGRITY_BASELINE_LOG_MODE"] = "db"
    os.environ["INTEGRITY_BASELINE_LOG_DB"] = db_path
    try:
        for i in range(3):
            now = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=i)).isoformat().replace('+00:00', 'Z')
            integrity_drift.append_baseline_log(
                table_name="audit_log",
                table_hash=f"hash{i}",
                schema_version="v1",
                created_at=now,
                hmac_sig=f"hmac{i}",
                db_path=db_path
            )
        # Truncate last entry
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("DELETE FROM baseline_log WHERE seq = (SELECT MAX(seq) FROM baseline_log)")
        conn.commit()
        cur.execute("SELECT COUNT(*) FROM baseline_log")
        count = cur.fetchone()[0]
        assert count == 2
        conn.close()
    finally:
        shutil.rmtree(tmpdir)
        os.environ.pop("INTEGRITY_BASELINE_LOG_MODE", None)
        os.environ.pop("INTEGRITY_BASELINE_LOG_DB", None)
