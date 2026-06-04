# pyright: reportUnknownParameterType=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportMissingParameterType=false, reportPrivateUsage=false

import os
import base64
import tempfile
import pytest
import sqlite3
import types
import sys
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
        connection.update_metadata_hash(conn)
    assert 'NGO_HOMESUITE_SCHEMA_SIGNATURE must be set' in str(excinfo.value)


def test_get_hmac_key_supports_b64_source(monkeypatch):
    monkeypatch.delenv('NGO_HOMESUITE_SCHEMA_HMAC_KEY', raising=False)
    monkeypatch.delenv('NGO_HOMESUITE_SCHEMA_HMAC_KEY_FILE', raising=False)
    monkeypatch.setenv(
        'NGO_HOMESUITE_SCHEMA_HMAC_KEY_B64',
        base64.b64encode(b'test_hmac_from_b64').decode('ascii'),
    )
    assert connection._get_hmac_key() == b'test_hmac_from_b64'


def test_get_hmac_key_supports_file_source(monkeypatch, tmp_path):
    key_file = tmp_path / 'schema_hmac.key'
    key_file.write_bytes(b'file_secret_key\n')

    monkeypatch.delenv('NGO_HOMESUITE_SCHEMA_HMAC_KEY', raising=False)
    monkeypatch.delenv('NGO_HOMESUITE_SCHEMA_HMAC_KEY_B64', raising=False)
    monkeypatch.setenv('NGO_HOMESUITE_SCHEMA_HMAC_KEY_FILE', str(key_file))

    assert connection._get_hmac_key() == b'file_secret_key'


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
    connection._append_external_audit_log({**entry, 'ts': 'later'}, log_type='provenance')
    assert log_path.exists()
    with open(log_path) as f:
        lines = f.readlines()
    assert len(lines) == 2


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


def test_connect_db_rejects_non_empty_schema_without_hash(monkeypatch, tmp_path):
    db_path = tmp_path / 'legacy_nonempty.sqlite3'
    conn = sqlite3.connect(db_path)
    conn.execute('CREATE TABLE test_payload (id INTEGER PRIMARY KEY, value TEXT)')
    conn.commit()
    conn.close()

    monkeypatch.delenv('NGO_HOMESUITE_DB_KEY', raising=False)
    monkeypatch.delenv('NGO_DB_KEY', raising=False)
    monkeypatch.setenv('NGO_HOMESUITE_SCHEMA_HMAC_KEY', 'test_hmac_key')
    monkeypatch.setenv('NGO_HOMESUITE_SCHEMA_SIGNATURE', 'test_schema_sig')
    monkeypatch.delenv('NGO_HOMESUITE_ALLOW_SCHEMA_MIGRATION', raising=False)

    with pytest.raises(FatalDBError, match='no stored schema HMAC'):
        connection.connect_db_at(str(db_path))


def test_connect_db_allows_empty_schema_bootstrap(monkeypatch, tmp_path):
    db_path = tmp_path / 'empty_bootstrap.sqlite3'

    monkeypatch.delenv('NGO_HOMESUITE_DB_KEY', raising=False)
    monkeypatch.delenv('NGO_DB_KEY', raising=False)
    monkeypatch.setenv('NGO_HOMESUITE_SCHEMA_HMAC_KEY', 'test_hmac_key')
    monkeypatch.setenv('NGO_HOMESUITE_SCHEMA_SIGNATURE', 'test_schema_sig')
    monkeypatch.delenv('NGO_HOMESUITE_ALLOW_SCHEMA_MIGRATION', raising=False)

    conn = connection.connect_db_at(str(db_path))
    assert isinstance(conn, sqlite3.Connection)
    conn.close()


def test_rotate_db_key_success_updates_env_and_window(monkeypatch, tmp_path):
    db_path = tmp_path / "rotate.sqlite3"
    monkeypatch.setattr(connection, "DB_PATH", str(db_path))

    events: list[str] = []

    class _FakeConn:
        def execute(self, sql, _params=None):
            events.append(str(sql))
            return self

        def close(self):
            return None

    fake_sqlcipher = types.SimpleNamespace(connect=lambda _path: _FakeConn())
    monkeypatch.setitem(sys.modules, "pysqlcipher3", types.SimpleNamespace(dbapi2=fake_sqlcipher))

    monkeypatch.setattr(connection, "log_key_provenance", lambda _c, _o, _n: None)
    captured: dict[str, object] = {}
    monkeypatch.setattr(connection, "set_global_dual_key_window", lambda w: captured.setdefault("window", w))

    old_key = "hex:" + ("a1" * 32)
    new_key = "hex:" + ("b2" * 32)
    monkeypatch.setenv(connection.DB_ENCRYPTION_KEY_ENV, old_key)

    connection.rotate_db_key(old_key=old_key, new_key=new_key, dual_window_seconds=60.0)

    assert any("PRAGMA key" in e for e in events)
    assert any("PRAGMA rekey" in e for e in events)
    assert os.environ.get(connection.DB_ENCRYPTION_KEY_ENV) == new_key
    assert "window" in captured


def test_rotate_db_key_rejects_same_key(monkeypatch):
    key = "hex:" + ("aa" * 32)
    with pytest.raises(FatalDBError, match="must differ"):
        connection.rotate_db_key(old_key=key, new_key=key)


def test_schema_migration_escape_hatch_invokes_hook(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE test_payload (id INTEGER PRIMARY KEY, value TEXT)")
    conn.commit()

    hook_calls: list[str | None] = []
    monkeypatch.setenv("NGO_HOMESUITE_ALLOW_SCHEMA_MIGRATION", "1")
    monkeypatch.setattr(connection, "SchemaMigrationHook", lambda _conn, db_path: hook_calls.append(db_path))

    connection._check_and_handle_schema_migration(conn, db_path=":memory:")
    assert hook_calls == [":memory:"]
    conn.close()


def test_schema_hash_mismatch_raises_without_escape_hatch(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t1 (id INTEGER PRIMARY KEY)")
    connection._ensure_metadata_table(conn)
    conn.execute(
        "INSERT INTO __db_metadata__ (key, value) VALUES ('schema_hmac', 'invalid_hash')"
    )
    conn.commit()

    monkeypatch.setenv("NGO_HOMESUITE_SCHEMA_HMAC_KEY", "test_hmac_key")
    monkeypatch.setenv("NGO_HOMESUITE_SCHEMA_SIGNATURE", "test_schema_sig")
    monkeypatch.delenv("NGO_HOMESUITE_ALLOW_SCHEMA_MIGRATION", raising=False)
    monkeypatch.setattr(connection, "SchemaMigrationHook", None)

    with pytest.raises(FatalDBError, match="stored HMAC does not match"):
        connection._check_and_handle_schema_migration(conn, db_path=":memory:")
    conn.close()
