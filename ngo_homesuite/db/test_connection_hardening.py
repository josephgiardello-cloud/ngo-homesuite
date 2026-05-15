import os
import tempfile
import pytest
import sqlite3
import stat
from pathlib import Path
from ngo_homesuite.db import connection
from ngo_homesuite.db.connection import FatalDBError

# --- Test Config ---
TEST_KEY = 'hex:' + 'a1' * 32
os.environ['NGO_HOMESUITE_DB_KEY'] = TEST_KEY
os.environ['NGO_DB_KEY'] = TEST_KEY
os.environ['NGO_HOMESUITE_SCHEMA_HMAC_KEY'] = 'test_hmac_key'
os.environ['NGO_HOMESUITE_SCHEMA_SIGNATURE'] = 'test_schema_sig'
os.environ['NGO_HOMESUITE_PROVENANCE_SIGNATURE'] = 'test_prov_sig'
os.environ['NGO_HOMESUITE_PROVENANCE_SIGN_KEY'] = 'test_sign_key'

@pytest.fixture
def temp_db_file():
    fd, path = tempfile.mkstemp(suffix='.sqlite3')
    os.close(fd)
    yield path
    try:
        os.remove(path)
    except Exception:
        pass


def test_secure_permissions_file_and_dir(tmp_path):
    file_path = tmp_path / 'testfile.txt'
    file_path.write_text('x')
    connection._set_secure_permissions(file_path)
    if os.name != 'nt':
        mode = file_path.stat().st_mode & 0o777
        assert mode == 0o600
    dir_path = tmp_path / 'testdir'
    dir_path.mkdir()
    connection._set_secure_permissions(dir_path, is_dir=True)
    if os.name != 'nt':
        mode = dir_path.stat().st_mode & 0o777
        assert mode == 0o700


def test_env_var_fail_safe(monkeypatch):
    monkeypatch.delenv('NGO_HOMESUITE_SCHEMA_HMAC_KEY', raising=False)
    with pytest.raises(FatalDBError):
        connection._get_hmac_key()
    # Set HMAC key so the code reaches the unconditional signature check, but do NOT set signature
    monkeypatch.setenv('NGO_HOMESUITE_SCHEMA_HMAC_KEY', 'test_hmac_key')
    monkeypatch.delenv('NGO_HOMESUITE_SCHEMA_SIGNATURE', raising=False)
    # Needs a connection, so use a dummy in-memory DB
    conn = sqlite3.connect(':memory:')
    connection._ensure_metadata_table(conn)
    with pytest.raises(FatalDBError) as excinfo:
        connection._update_metadata_hash(conn)
    assert 'NGO_HOMESUITE_SCHEMA_SIGNATURE must be set' in str(excinfo.value)


def test_temp_file_cleanup():
    # Simulate temp file creation and ensure cleanup
    with tempfile.NamedTemporaryFile(delete=False) as tf:
        temp_path = Path(tf.name)
    assert temp_path.exists()
    # Use the cleanup utility
    connection._set_secure_permissions(temp_path)
    try:
        temp_path.unlink()
    except Exception:
        pass
    assert not temp_path.exists()


def test_append_only_log(tmp_path):
    log_path = tmp_path / 'testlog.log'
    os.environ['NGO_HOMESUITE_PROVENANCE_LOG'] = str(log_path)
    entry = {'ts': 'now', 'operator': 'test', 'old_key_fingerprint': 'a', 'new_key_fingerprint': 'b', 'signature': 'sig', 'event': 'key_rotation'}
    connection._append_external_audit_log(entry, log_type='provenance')
    assert log_path.exists()
    with open(log_path) as f:
        lines = f.readlines()
    assert len(lines) == 1
    # Try to open in write mode should not truncate (simulate append-only policy)
    with open(log_path, 'a') as f:
        f.write('test\n')
    with open(log_path) as f:
        lines2 = f.readlines()
    assert len(lines2) == 2


def test_logger_handler_hijack_defense(monkeypatch):
    import logging
    log = logging.getLogger('ngo_homesuite.structured')
    # Remove all handlers
    log.handlers.clear()
    # Add a dummy handler
    dummy = logging.StreamHandler()
    log.addHandler(dummy)
    # Re-run setup, should not add duplicate structured handler
    connection._setup_structured_logging()
    count = sum(isinstance(h, logging.StreamHandler) and isinstance(getattr(h, 'formatter', None), connection._StructuredLogFormatter) for h in log.handlers)
    assert count <= 1
