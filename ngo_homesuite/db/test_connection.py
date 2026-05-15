import os
import tempfile
import pytest
import sqlite3
import time
from pathlib import Path
from ngo_homesuite.db import connection

# --- Test Config ---
TEST_DB_PATH = os.environ.get('TEST_DB_PATH', 'test_connection_py.sqlite3')
TEST_KEY = 'hex:' + 'a1' * 32  # 64 hex chars
TEST_KEY_2 = 'hex:' + 'b2' * 32
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


def test_connect_db_and_schema_hash(temp_db_file):
    # Should create and connect to a new DB, set up schema hash
    try:
        import pysqlcipher3  # noqa: F401
    except ImportError:
        pytest.skip("SQLCipher not installed; skipping test.")
    conn = connection.connect_db_at(temp_db_file)
    assert isinstance(conn, sqlite3.Connection)
    # Should create metadata table and set schema hash
    connection._ensure_metadata_table(conn)
    connection.update_metadata_hash(conn)
    assert connection.check_metadata_hash(conn) is True
    conn.close()


def test_sqlcipher_key_policy_enforcement(monkeypatch, temp_db_file):
    # Should enforce hex key if required
    monkeypatch.setenv('NGO_HOMESUITE_DB_REQUIRE_HEX_KEY', '1')
    monkeypatch.setenv('NGO_HOMESUITE_DB_KEY', 'nothex')
    # Should raise ValueError when applying key, not just policy
    with pytest.raises(ValueError):
        connection._sqlcipher_apply_key(sqlite3.connect(':memory:'), 'nothex')
    monkeypatch.setenv('NGO_HOMESUITE_DB_KEY', TEST_KEY)
    # Should not raise now
    connection._sqlcipher_policy()


def test_fataldberror_on_missing_sqlcipher(monkeypatch, temp_db_file):
    # Simulate missing pysqlcipher3
    monkeypatch.setitem(os.sys.modules, 'pysqlcipher3', None)
    monkeypatch.delenv('NGO_HOMESUITE_DB_KEY', raising=False)
    monkeypatch.delenv('NGO_DB_KEY', raising=False)
    # Should fallback to sqlite3, not raise
    conn = connection.connect_db_at(temp_db_file)
    assert isinstance(conn, sqlite3.Connection)
    conn.close()


def test_run_db_transient_error(monkeypatch, temp_db_file):
    # Simulate transient error (locked)
    def op(conn, cur):
        raise sqlite3.OperationalError('database is locked')
    with pytest.raises(connection.FatalDBError):
        connection.run_db(op, retries=2, base_delay_s=0.01)


def test_backup_corrupt_db_copy(temp_db_file):
    # Should create a backup file if DB exists
    Path(temp_db_file).touch()
    backup = connection._backup_corrupt_db_copy()
    assert backup is not None
    assert Path(backup).exists()
    Path(backup).unlink()


def test_dual_key_window_logic():
    # Test DualKeyWindow activation and expiry
    win = connection.DualKeyWindow('old', 'new', time.time() + 1)
    assert win.is_active() is True
    time.sleep(1.1)
    assert win.is_active() is False


def test_attach_database_blocked(temp_db_file):
    # ATTACH DATABASE should be blocked by policy
    conn = sqlite3.connect(temp_db_file)
    connection._install_attach_hardening(conn)
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("ATTACH DATABASE 'foo.db' AS foo")
    conn.close()
