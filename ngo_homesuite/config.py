from __future__ import annotations

import os

# Core settings / constants

DB_PATH = os.environ.get("NGOHS_DB_PATH", "ngo_database.db")
TENANT = os.environ.get("NGOHS_TENANT", "default")
DB_ENCRYPTION_KEY_ENV = "NGOHS_DB_KEY"

DEFAULT_BACKUP_DIR = "backups"
DEFAULT_EXPORT_DIR = "exports"
BACKUP_REMINDER_DAYS = 7

PBKDF2_ITERATIONS = 260_000
MAX_LOGIN_ATTEMPTS = 5
LOGIN_BACKOFF_BASE_SECONDS = 0.5

MAX_EMAIL_LENGTH = 254
PHONE_MIN_DIGITS = 7
PHONE_MAX_DIGITS = 15
