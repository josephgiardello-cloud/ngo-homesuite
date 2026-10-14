import os
import secrets
import sqlite3
import types

import pytest

from ngo_homesuite.db import connection


@pytest.fixture
def cipher_driver(monkeypatch, tmp_path):
    try:
        driver = connection._load_sqlcipher_driver()
    except connection.FatalDBError:
        if os.environ.get("NGO_HOMESUITE_REQUIRE_SQLCIPHER_TESTS") == "1":
            pytest.fail("Encrypted CI requires a real SQLCipher driver")
        pytest.skip("No SQLCipher driver on this platform")
    monkeypatch.setenv("NGO_HOMESUITE_SCHEMA_HMAC_KEY", secrets.token_hex(32))
    monkeypatch.setenv("NGO_HOMESUITE_SCHEMA_SIGNATURE", "synthetic-test-signature")
    monkeypatch.setenv("NGO_HOMESUITE_SCHEMA_LOG", str(tmp_path / "schema.log"))
    monkeypatch.setenv("NGO_HOMESUITE_PROVENANCE_LOG", str(tmp_path / "provenance.log"))
    monkeypatch.setenv("NGO_HOMESUITE_ALLOW_SCHEMA_MIGRATION", "1")
    monkeypatch.setattr(connection, "DB_POOL_SIZE", 0)
    monkeypatch.setattr(connection, "_dual_key_window", None)
    return driver


def test_plain_sqlite_driver_cannot_claim_encryption():
    conn = sqlite3.connect(":memory:")
    try:
        with pytest.raises(connection.FatalDBError, match="SQLCipher support"):
            connection._verify_sqlcipher_driver(conn)
    finally:
        conn.close()


def test_missing_drivers_refuse_plaintext_fallback(monkeypatch):
    def missing(name):
        raise ModuleNotFoundError("synthetic missing driver", name=name)

    monkeypatch.setattr(connection, "import_module", missing)
    with pytest.raises(connection.FatalDBError, match="Refusing unencrypted fallback"):
        connection._load_sqlcipher_driver()


def test_modern_driver_preferred_and_legacy_installation_supported(monkeypatch):
    driver = types.ModuleType("synthetic_sqlcipher")
    driver.DatabaseError = sqlite3.DatabaseError
    calls = []

    def modern(name):
        calls.append(name)
        return types.SimpleNamespace(dbapi2=driver)

    monkeypatch.setattr(connection, "import_module", modern)
    assert connection._load_sqlcipher_driver() is driver
    assert calls == ["sqlcipher3"]

    def legacy(name):
        if name == "sqlcipher3":
            raise ModuleNotFoundError("missing", name=name)
        return types.SimpleNamespace(dbapi2=driver)

    monkeypatch.setattr(connection, "import_module", legacy)
    assert connection._load_sqlcipher_driver() is driver


@pytest.mark.sqlcipher
@pytest.mark.parametrize("key_mode", ["hex", "passphrase"])
def test_real_encryption_reopen_wrong_key_and_plaintext_denial(
    cipher_driver, monkeypatch, tmp_path, key_mode
):
    path = tmp_path / "encrypted.sqlite3"
    key = (
        "hex:" + secrets.token_hex(32)
        if key_mode == "hex"
        else "synthetic '" + secrets.token_hex(24)
    )
    monkeypatch.setenv(connection.DB_ENCRYPTION_KEY_ENV, key)
    monkeypatch.setenv("NGO_HOMESUITE_DB_REQUIRE_HEX_KEY", "0")
    conn = connection.connect_db_at(str(path))
    assert conn.execute("PRAGMA cipher_version").fetchone()[0]
    conn.execute("CREATE TABLE private_money (amount INTEGER NOT NULL)")
    conn.execute("INSERT INTO private_money VALUES (12345)")
    conn.commit()
    connection.update_metadata_hash(conn)
    monkeypatch.delenv("NGO_HOMESUITE_ALLOW_SCHEMA_MIGRATION")
    conn.close()
    assert path.read_bytes()[:16] != b"SQLite format 3\x00"
    plaintext = sqlite3.connect(path)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            plaintext.execute("SELECT amount FROM private_money").fetchone()
    finally:
        plaintext.close()
    reopened = connection.connect_db_at(str(path))
    assert reopened.execute("SELECT amount FROM private_money").fetchone() == (12345,)
    reopened.close()
    monkeypatch.setenv(connection.DB_ENCRYPTION_KEY_ENV, "hex:" + secrets.token_hex(32))
    with pytest.raises(connection.FatalDBError, match="Failed to open encrypted DB"):
        connection.connect_db_at(str(path))


@pytest.mark.sqlcipher
def test_real_key_rotation_reopens_with_new_key_rejects_old_and_preserves_records(
    cipher_driver, monkeypatch, tmp_path
):
    path = tmp_path / "rotation.sqlite3"
    old_key = "hex:" + secrets.token_hex(32)
    new_key = "hex:" + secrets.token_hex(32)
    monkeypatch.setattr(connection, "DB_PATH", str(path))
    monkeypatch.setenv(connection.DB_ENCRYPTION_KEY_ENV, old_key)
    conn = connection.connect_db_at(str(path))
    conn.execute("CREATE TABLE private_money (amount INTEGER NOT NULL)")
    conn.execute("INSERT INTO private_money VALUES (12345)")
    conn.commit()
    connection._ensure_provenance_table(conn)
    connection.update_metadata_hash(conn)
    monkeypatch.delenv("NGO_HOMESUITE_ALLOW_SCHEMA_MIGRATION")
    conn.close()
    connection.rotate_db_key(old_key=old_key, new_key=new_key)
    assert os.environ[connection.DB_ENCRYPTION_KEY_ENV] == new_key
    connection.set_global_dual_key_window(None)
    reopened = connection.connect_db_at(str(path))
    assert reopened.execute("SELECT amount FROM private_money").fetchone() == (12345,)
    reopened.close()
    monkeypatch.setenv(connection.DB_ENCRYPTION_KEY_ENV, old_key)
    with pytest.raises(connection.FatalDBError):
        connection.connect_db_at(str(path))
