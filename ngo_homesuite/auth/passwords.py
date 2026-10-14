from __future__ import annotations

import hashlib
import hmac
import secrets

from ..config import PBKDF2_ITERATIONS


def password_hash(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)


def encode_password(password: str) -> tuple[str, str]:
    if len(password) < 10 or len(password) > 1024:
        raise ValueError("Password must be between 10 and 1024 characters")
    salt = secrets.token_bytes(16)
    return salt.hex(), password_hash(password, salt).hex()


def verify_password(password: str, salt_hex: str, expected_hash_hex: str) -> bool:
    return hmac.compare_digest(password_hash(password, bytes.fromhex(salt_hex)), bytes.fromhex(expected_hash_hex))
