"""
Sample unit tests for ngo_homesuite.auth.models
Run with: pytest test_auth_models.py
"""
import pytest
import sqlite3
import os
import tempfile
from argon2 import PasswordHasher, Type
from ngo_homesuite.auth import models

def setup_in_memory_db():
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    # Minimal users table for testing
    cur.execute("""
        CREATE TABLE users (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            created_at TEXT
        )
    """)
    conn.commit()
    return conn, cur

def test_enforce_password_policy_strong(monkeypatch):
    # Should not raise for a strong password
    username = "testuser"
    password = "ThisIsAStrongPassword123!@#"
    # Patch _check_pwned to always return False (not pwned)
    monkeypatch.setattr(models, "_check_pwned", lambda *a, **kw: False)
    models._enforce_password_policy(username, password, role="admin")

def test_enforce_password_policy_weak(monkeypatch):
    username = "testuser"
    password = "password123"
    monkeypatch.setattr(models, "_check_pwned", lambda *a, **kw: False)
    # The actual error message for short passwords is 'Password must be at least 12 characters long.'
    with pytest.raises(ValueError, match="at least 12 characters"):
        models._enforce_password_policy(username, password, role="admin")

def test_enforce_password_policy_pwned(monkeypatch):
    username = "testuser"
    password = "ThisIsAStrongPassword123!@#"
    # Patch _check_pwned to return True (pwned)
    monkeypatch.setattr(models, "_check_pwned", lambda *a, **kw: True)
    monkeypatch.setattr(models, "audit", lambda *a, **kw: None)
    with pytest.raises(ValueError, match="appeared in a public breach"):
        models._enforce_password_policy(username, password, role="admin")

def test_create_user_and_verify(monkeypatch):
    # Setup: create a temp sqlite DB and users table
    monkeypatch.setattr(models, "_check_pwned", lambda *a, **kw: False)
    monkeypatch.setattr(models, "audit", lambda *a, **kw: None)
    monkeypatch.setattr(
        models,
        "ARGON2_PH",
        PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1, hash_len=16, salt_len=8, type=Type.ID),
    )

    with tempfile.NamedTemporaryFile(suffix='.sqlite3', delete=False) as tf:
        db_path = tf.name
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()
        # Test user creation
        username = "testuser"
        password = "SuperSecurePassword123!"
        role = "admin"
        try:
            models.create_user(conn, cur, username, password, role)
        except Exception as e:
            print(f"create_user raised: {e}")
            raise
        cur.execute("SELECT password_hash FROM users WHERE username=?", (username,))
        row = cur.fetchone()
        print(f"User row: {row}")
        assert row, "User not created"
        hashval = row[0]
        # Test password verification
        assert models.verify_password(password, hashval)[0], "Password verification failed"
        assert not models.verify_password("wrongpassword", hashval)[0], "Wrong password should not verify"
        print("test_create_user_and_verify: PASS")
        conn.close()
    finally:
        try:
            os.unlink(db_path)
        except Exception as e:
            print(f"Could not delete temp DB: {e}")


def test_authenticate_user_returns_identity(monkeypatch):
    monkeypatch.setattr(models, "_check_pwned", lambda *a, **kw: False)
    monkeypatch.setattr(models, "audit", lambda *a, **kw: None)
    monkeypatch.setattr(
        models,
        "ARGON2_PH",
        PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1, hash_len=16, salt_len=8, type=Type.ID),
    )

    with tempfile.NamedTemporaryFile(suffix='.sqlite3', delete=False) as tf:
        db_path = tf.name
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()
        models.create_user(conn, cur, "testuser", "SuperSecurePassword123!", "admin")
        monkeypatch.setattr(models, "run_db", lambda op: op(conn, conn.cursor()))

        user = models.authenticate_user("testuser", "SuperSecurePassword123!")

        assert user["username"] == "testuser"
        assert user["role"] == "admin"
        assert isinstance(user["id"], int)
        conn.close()
    finally:
        try:
            os.unlink(db_path)
        except Exception as e:
            print(f"Could not delete temp DB: {e}")

if __name__ == "__main__":
    test_create_user_and_verify()
