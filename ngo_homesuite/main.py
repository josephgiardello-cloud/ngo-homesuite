# --- Clean, robust main.py ---

# --- Standard library imports ---
import argparse
import logging
import os
import sys
import uuid
import datetime
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

# Support running as a script: python ngo_homesuite/main.py
if __package__ in (None, ""):
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

# --- Third-party imports ---
import yaml
from flask import Flask, request, render_template, session as flask_session
from flask_babel import Babel, gettext
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.ext.declarative import declarative_base

# --- Local/project imports ---
from ngo_homesuite.db.engine import init_engine
from ngo_homesuite.services.reporting_service import ReportingService

## SQLAlchemy models and fetch_reports have been moved to db/models.py and db/repositories/reports.py


logger = logging.getLogger("ngo_homesuite")
START_CWD = Path.cwd()

# --- Append-only Audit Log Table ---
# This table records all entity changes (insert, update, delete) for auditability.
# It is append-only: no update or delete logic is provided for audit_log entries.

from sqlalchemy import Column, Integer, String, DateTime, Text
from sqlalchemy.ext.declarative import declarative_base


from sqlalchemy import event
Base = declarative_base()

class AuditLog(Base):
    __tablename__ = "audit_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    entity = Column(String(64), nullable=False)
    entity_id = Column(String(64), nullable=False)
    action = Column(String(32), nullable=False)  # e.g., 'insert', 'update', 'delete'
    actor = Column(String(64), nullable=True)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    details = Column(Text, nullable=True)  # JSON or string with change details
    hash_prev = Column(String(64), nullable=True)
    hash_event = Column(String(64), nullable=False)


# --- Audit log read-only enforcement ---

# --- Unified audit log and critical entity immutability enforcement ---

# --- Decoupled AuditLog immutability enforcement for testability ---
class AuditLogReadonlyError(Exception):
    """Raised when an attempt is made to write to audit_log or critical entities in read-only or immutable mode."""
    pass

def should_block_auditlog_write(tablename, op, readonly_mode=None):
    """
    Determine if a write operation should be blocked for audit_log or critical entities.
    - tablename: str, table name
    - op: str, operation type ('before_insert', 'before_update', 'before_delete')
    - readonly_mode: bool or None. If None, will check env var NGO_AUDIT_READONLY.
    """
    if readonly_mode is None:
        readonly_mode = os.getenv("NGO_AUDIT_READONLY", "0").lower() in ("1", "true", "yes", "on")
    # Block UPDATE/DELETE on audit_log if read-only
    if readonly_mode and tablename == "audit_log" and op in ("before_update", "before_delete"):
        return True, f"audit_log is read-only (NGO_AUDIT_READONLY=1); {op.upper()} forbidden"
    # Always block direct UPDATE/DELETE for critical entities
    if tablename in ("audit_log", "donor", "donation") and op in ("before_update", "before_delete"):
        return True, f"Direct {op.upper()} is forbidden for critical entity {tablename}"
    return False, None

def _enforce_auditlog_immutability(mapper, connection, target):
    # Use SQLAlchemy event arguments for operation type
    tablename = getattr(target, '__tablename__', type(target).__name__)
    # Try to get the event name from the call stack (fragile fallback)
    import inspect
    frame = inspect.currentframe()
    op = None
    while frame:
        code = frame.f_code.co_name
        if code in ("before_insert", "before_update", "before_delete"):
            op = code
            break
        frame = frame.f_back
    if not op:
        op = "unknown"
    block, reason = should_block_auditlog_write(tablename, op)
    if block:
        logger.warning("Blocked attempt on %s: %s", tablename, target)
        raise AuditLogReadonlyError(f"{reason}: {target}")

import threading
_last_cross_hash_time = [0]
_cross_hash_lock = threading.Lock()

def log_audit(session, entity, entity_id, action, actor=None, details=None):
    """Append an audit log entry for an entity change with hash chaining. Optionally log cross-table hash periodically (non-blocking)."""
    # Get previous hash
    prev = session.query(AuditLog).order_by(AuditLog.id.desc()).first()
    hash_prev = prev.hash_event if prev else None
    # Prepare event string for hashing (explicit UTC tzinfo)
    now_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
    event_str = f"{entity}|{entity_id}|{action}|{actor}|{details}|{hash_prev or ''}|{now_utc}"
    hash_event = hashlib.sha256(event_str.encode('utf-8')).hexdigest()
    entry = AuditLog(
        entity=entity,
        entity_id=str(entity_id),
        action=action,
        actor=actor,
        details=details,
        hash_prev=hash_prev,
        hash_event=hash_event
    )
    session.add(entry)
    # Optionally log cross-table hash periodically (if NGO_CROSS_HASH_PERIOD_SEC is set), non-blocking
    import os, time, threading
    period = float(os.getenv("NGO_CROSS_HASH_PERIOD_SEC", "0"))
    if period > 0:
        now = time.time()
        def _background_cross_hash():
            with _cross_hash_lock:
                compute_cross_table_hash(session)
                _last_cross_hash_time[0] = now
        if now - _last_cross_hash_time[0] >= period:
            t = threading.Thread(target=_background_cross_hash, daemon=True)
            t.start()


# --- Enforce audit_log and critical entity immutability with unified handler (loop version) ---
for cls in [AuditLog]:
    event.listen(cls, 'before_insert', _enforce_auditlog_immutability)
    event.listen(cls, 'before_update', _enforce_auditlog_immutability)
    event.listen(cls, 'before_delete', _enforce_auditlog_immutability)

try:
    from ngo_homesuite.db.models import Donor, Donation
    for cls in [Donor, Donation]:
        event.listen(cls, 'before_update', _enforce_auditlog_immutability)
        event.listen(cls, 'before_delete', _enforce_auditlog_immutability)
except ImportError:
    pass  # Models may not be available at main.py import time

def compute_cross_table_hash(session):
    """Compute and log cross-table hash for Donor and Donation."""
    try:
        import hashlib
        from ngo_homesuite.db.models import Donor, Donation
        with _cross_hash_lock:
            donor_hash = hashlib.sha256()
            for donor in session.query(Donor.id, Donor.name):
                donor_hash.update(f"{donor.id}{donor.name or ''}".encode('utf-8'))
            donor_digest = donor_hash.hexdigest()

            donation_hash = hashlib.sha256()
            for donation in session.query(Donation.id, Donation.amount):
                donation_hash.update(f"{donation.id}{donation.amount}".encode('utf-8'))
            donation_digest = donation_hash.hexdigest()

            cross_hash = hashlib.sha256((donor_digest + donation_digest).encode('utf-8')).hexdigest()
            logger.info(f"[CROSS-TABLE HASH] Donor: {donor_digest}, Donation: {donation_digest}, Combined: {cross_hash}")
            return {'donor_hash': donor_digest, 'donation_hash': donation_digest, 'cross_hash': cross_hash}
    except Exception as e:
        logger.error(f"[CROSS-TABLE HASH] Failed to compute: {e}")
        return None

# --- Print recommended SQL triggers for DB-level immutability ---
def print_auditlog_sql_triggers():
    print("\nRecommended SQL triggers for DB-level immutability (SQLite):\n")
    print("""
--
-- WARNING: Setting NGO_AUDIT_READONLY=1 enables forensic mode. All writes to audit_log are blocked. Use only for replicas or forensic analysis.
--
CREATE TRIGGER audit_log_no_update
BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is read-only; UPDATE forbidden');
END;

CREATE TRIGGER audit_log_no_delete
BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is read-only; DELETE forbidden');
END;
""")
    print("For PostgreSQL, use BEFORE UPDATE/DELETE triggers with RAISE EXCEPTION.")
# Usage: log_audit(session, 'Donor', donor.id, 'update', actor='admin', details='{"field": "value"}')

# --- Config schema ---
class SettingsModel(BaseModel):
    db_path: str = Field(default="ngo_data.db")
    backup_directory: str = Field(default="backups/")
    log_level: str = Field(default="INFO")
    skip_backup_reminder: bool = Field(default=False)
    strict_env_overrides: bool = Field(default=False)
    path_sandbox_roots_raw: List[str] = Field(default_factory=list)

@dataclass
class Settings:
    db_path: str = "ngo_data.db"
    backup_directory: str = "backups/"
    log_level: str = "INFO"
    skip_backup_reminder: bool = False
    strict_env_overrides: bool = False
    path_sandbox_roots_raw: list = field(default_factory=list)

def _get_nested(data, *keys):
    for key in keys:
        if isinstance(data, dict):
            data = data.get(key)
        else:
            return None
    return data

def _env_bool(var_name: str) -> Optional[bool]:
    val = os.getenv(var_name)
    if val is None: return None
    return val.lower() in ("1", "true", "yes", "on")

def _effective_settings(config: dict) -> Settings:
    return Settings(
        db_path=_get_nested(config, "database", "path") or "ngo_data.db",
        backup_directory=_get_nested(config, "backup", "directory") or "backups/",
        log_level=_get_nested(config, "logging", "level") or "INFO",
        skip_backup_reminder=_get_nested(config, "startup", "skip_backup_reminder") or False,
        strict_env_overrides=_get_nested(config, "security", "strict_env_overrides") or False,
        path_sandbox_roots_raw=_get_nested(config, "security", "path_sandbox_roots") or []
    )

def _validate_config(config):
    try:
        SettingsModel(**{
            'db_path': _get_nested(config, "database", "path") or "ngo_data.db",
            'backup_directory': _get_nested(config, "backup", "directory") or "backups/",
            'log_level': _get_nested(config, "logging", "level") or "INFO",
            'skip_backup_reminder': _get_nested(config, "startup", "skip_backup_reminder") or False,
            'strict_env_overrides': _get_nested(config, "security", "strict_env_overrides") or False,
            'path_sandbox_roots_raw': _get_nested(config, "security", "path_sandbox_roots") or []
        })
    except ValidationError as e:
        raise ValueError(f"Config validation error: {e}")

def _resolve_config_path(config_arg):
    return config_arg, bool(config_arg)

def _configure_logging(log_level):
    logging.basicConfig(
        level=getattr(logging, str(log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger.setLevel(getattr(logging, str(log_level).upper(), logging.INFO))

# --- Auth ---
class AuthError(Exception):
    pass

def login():
    username = request.form.get('username')
    password = request.form.get('password')
    if username == 'admin' and password == os.getenv('ADMIN_PASSWORD', 'admin'):
        flask_session['user'] = username
        class User:
            name = username
        return User()
    raise AuthError("Invalid credentials.")

def main_menu():
    print("[Main menu placeholder]")

def _ensure_db_parent_dir(db_path: str) -> None:
    import stat
    raw = db_path.strip()
    if not raw:
        return
    if raw == ":memory:" or raw.lower().startswith("file:"):
        if os.getenv("NGO_HOMESUITE_STRICT_ENV_OVERRIDES", "0") in ("1", "true", "yes", "on"):
            logger.warning("strict_env_overrides is enabled, but database is set to ':memory:'. All audit logs will be lost on restart.")
        return
    allowed_roots = os.getenv("NGO_HOMESUITE_ALLOWED_ROOTS", str(START_CWD)).split(os.pathsep)
    resolved = Path(raw).expanduser().resolve(strict=False)
    parent = resolved.parent
    if str(parent) in {".", ""}:
        return
    # Canonicalize parent and allowed roots
    try:
        parent_real = parent.resolve(strict=False)
        # Use os.fstat for symlink safety
        parent_fd = os.open(str(parent_real), os.O_RDONLY)
        try:
            st = os.fstat(parent_fd)
            if hasattr(stat, 'S_IFLNK') and stat.S_IFMT(st.st_mode) == stat.S_IFLNK:
                raise PermissionError(f"Database directory {parent_real} is a symlink (os.fstat), which is forbidden.")
        finally:
            os.close(parent_fd)
        if parent.is_symlink():
            raise PermissionError(f"Database directory {parent} is a symlink, which is forbidden.")
        allowed_real = [Path(root).expanduser().resolve(strict=False) for root in allowed_roots]
        if not any(parent_real == ar or str(parent_real).startswith(str(ar) + os.sep) for ar in allowed_real):
            raise PermissionError(f"Database path {parent_real} is not within allowed roots.")
    except Exception as e:
        logger.error(f"Symlink/canonical path check failed for {parent}: {e}")
        raise PermissionError(f"Symlink/canonical path check failed for {parent}: {e}")
    existed = parent.exists()
    parent.mkdir(parents=True, exist_ok=True)
    if not existed:
        logger.info("Created database directory: %s", parent)
    probe = parent / f".ngo_homesuite_write_probe_{uuid.uuid4().hex}"
    try:
        with open(probe, "xb"):
            pass
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass
    except Exception as e:
        logger.error(f"Failed to create probe file {probe}: {e}")
        raise PermissionError(f"Cannot write to database directory {parent}: {e}")

# --- Auto-migrate on startup ---

from ngo_homesuite.db.migrate import auto_migrate

# --- Flask app ---
app = Flask(__name__)
app.config['LANGUAGES'] = ['en', 'es', 'fr', 'hi']
app.config['BABEL_DEFAULT_LOCALE'] = 'en'
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'dev')
def get_locale():
    return request.accept_languages.best_match(app.config['LANGUAGES'])

babel = Babel(app, locale_selector=get_locale)

@app.route('/')
def index():
    return render_template('index.html', welcome=gettext('Welcome to NGO Homesuite'))

reporting_service = ReportingService()

@app.route('/api/report/<report_type>', methods=['GET'])
def api_report(report_type):
    try:
        params = dict(request.args)
        actor = flask_session.get('user', 'api')
        data = reporting_service.generate_report(report_type, params=params, actor=actor)
        return {"status": "ok", "data": data}
    except Exception as e:
        logger.exception(f"Error generating report {report_type}")
        return {"status": "error", "error": str(e)}, 400

# --- Main entry ---


# --- Refactored main logic into smaller functions ---
def load_config(argv):
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--config")
    pre_args, _ = pre_parser.parse_known_args(argv)
    config_path, config_required = _resolve_config_path(pre_args.config)
    try:
        config, loaded_config_path = _load_yaml_config(config_path, required=config_required)
    except Exception as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2)
    if config:
        try:
            _validate_config(config)
        except ValueError as exc:
            print(f"Configuration error: {exc}", file=sys.stderr)
            raise SystemExit(2)
    return config

def setup_logging(log_level):
    _configure_logging(log_level)
    logger.info("Sandbox root CWD = %s at startup.", START_CWD)

def check_env_and_secrets(argv):
    admin_pw = os.getenv('ADMIN_PASSWORD')
    flask_secret = os.getenv('FLASK_SECRET_KEY')
    is_debug = os.getenv('FLASK_ENV') == 'development' or os.getenv('NGO_HOMESUITE_DEBUG') == '1'
    is_test = '--test' in argv if argv else False
    if not admin_pw and not (is_debug or is_test):
        print("ERROR: ADMIN_PASSWORD environment variable must be set in production.", file=sys.stderr)
        raise SystemExit(3)
    if not flask_secret and not (is_debug or is_test):
        print("ERROR: FLASK_SECRET_KEY environment variable must be set in production.", file=sys.stderr)
        raise SystemExit(4)

def run_migrations_and_db(eff):
    auto_migrate(eff.db_path)
    init_engine(eff.db_path)

def run_integrity_check(eff):
    import shutil
    try:
        from ngo_homesuite.utils.integrity_drift import periodic_integrity_check
    except Exception:
        logger.warning("[INTEGRITY] periodic_integrity_check is unavailable; skipping integrity drift check.")
        return
    alert_email = os.getenv('NGO_INTEGRITY_ALERT_EMAIL')
    alert_webhook = os.getenv('NGO_INTEGRITY_ALERT_WEBHOOK')
    alert_slack = os.getenv('NGO_INTEGRITY_ALERT_SLACK')
    stats_log_path = os.getenv('NGO_INTEGRITY_STATS_LOG', 'integrity_stats.log')
    # Rotate stats log if >10MB (like webhook logs)
    try:
        if os.path.exists(stats_log_path) and os.path.getsize(stats_log_path) > 10 * 1024 * 1024:
            shutil.move(stats_log_path, stats_log_path + ".1")
            logger.info(f"Rotated integrity stats log: {stats_log_path} -> {stats_log_path}.1")
    except Exception as e:
        logger.warning(f"Could not rotate integrity stats log: {e}")
    if alert_webhook:
        logger.warning("[SECURITY] alert_webhook is set. If the webhook URL is compromised, it could leak sensitive info. Review your alerting configuration.")
    # Warn if audit_log is in read-only mode
    if os.getenv("NGO_AUDIT_READONLY", "0").lower() in ("1", "true", "yes", "on"):
        logger.warning("audit_log is in read-only mode (NGO_AUDIT_READONLY=1): all writes are forbidden.")
    try:
        from sqlalchemy import inspect, create_engine
        engine = create_engine(f"sqlite:///{eff.db_path}")
        inspector = inspect(engine)
        if 'audit_log' in inspector.get_table_names():
            periodic_integrity_check(eff.db_path, alert_email=alert_email, alert_webhook=alert_webhook, alert_slack=alert_slack, stats_log_path=stats_log_path)
        else:
            logger.warning("[INTEGRITY] Skipping periodic_integrity_check: audit_log table does not exist.")
    except Exception as e:
        logger.warning(f"[INTEGRITY] Could not check or run periodic_integrity_check: {e}")

def compute_code_hash():
    try:
        import hashlib
        code_path = None
        if '__file__' in globals():
            code_path = __file__
        elif len(sys.argv) > 0:
            code_path = sys.argv[0]
        if code_path and os.path.isfile(code_path):
            h = hashlib.sha256()
            with open(code_path, 'rb') as f:
                while True:
                    chunk = f.read(1024 * 1024)
                    if not chunk:
                        break
                    h.update(chunk)
            hash_val = h.hexdigest()
            logger.info(f"[CODE HASH] SHA256 for {code_path}: {hash_val}")
        else:
            logger.info("[CODE HASH] Skipped: code file not found or running in interactive/frozen mode.")
    except Exception as e:
        logger.warning("Could not compute code hash: %s", e)

def run_cli_menu():
    logger.info("Entering main menu (CLI mode, no login)")
    print("Welcome, user!")
    main_menu()

def main(argv=None):
    argv = argv if argv is not None else []
    compute_code_hash()
    config = load_config(argv)
    eff = _effective_settings(config)
    setup_logging(eff.log_level)
    check_env_and_secrets(argv)
    run_migrations_and_db(eff)
    run_integrity_check(eff)
    # Only run main_menu in CLI if running interactively (not web or test); do not use login() in CLI
    if sys.stdin.isatty() and not any(arg in argv for arg in ["--web", "--test"]):
        run_cli_menu()

def _load_yaml_config(path: Optional[str], required: bool = False):
    if not path:
        return {}, None
    try:
        with open(path, 'r') as f:
            return yaml.safe_load(f) or {}, path
    except FileNotFoundError:
        if required: raise
        return {}, None

# --- TESTS ---
import unittest
class TestConfigHelpers(unittest.TestCase):
    def test_resolve_config_path(self):
        self.assertEqual(_resolve_config_path(None), (None, False))
        self.assertEqual(_resolve_config_path('foo.yaml'), ('foo.yaml', True))

    def test_effective_settings_defaults(self):
        s = _effective_settings({})
        self.assertEqual(s.db_path, 'ngo_data.db')
        self.assertEqual(s.backup_directory, 'backups/')
        self.assertEqual(s.log_level, 'INFO')
        self.assertFalse(s.skip_backup_reminder)
        self.assertFalse(s.strict_env_overrides)
        self.assertEqual(s.path_sandbox_roots_raw, [])

    def test_env_bool(self):
        os.environ['TEST_BOOL'] = 'yes'
        self.assertTrue(_env_bool('TEST_BOOL'))
        os.environ['TEST_BOOL'] = 'no'
        self.assertFalse(_env_bool('TEST_BOOL'))
        del os.environ['TEST_BOOL']
        self.assertIsNone(_env_bool('TEST_BOOL'))

    def test_ensure_db_parent_dir_allowed(self):
        allowed = str(START_CWD)
        test_db = str(Path(allowed) / 'test.db')
        os.environ['NGO_HOMESUITE_ALLOWED_ROOTS'] = allowed
        _ensure_db_parent_dir(test_db)
        Path(test_db).unlink(missing_ok=True)

    def test_ensure_db_parent_dir_denied(self):
        os.environ['NGO_HOMESUITE_ALLOWED_ROOTS'] = str(START_CWD)
        with self.assertRaises(PermissionError):
            _ensure_db_parent_dir('/etc/passwd')


# --- Clean separation of CLI and Flask app entrypoints ---
def run_cli():
    main(sys.argv[1:])

def run_web():
    from ngo_homesuite.app_factory import create_app

    flask_app = create_app()
    host = os.environ.get('HOST', '127.0.0.1')
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', 'True').lower() == 'true'
    flask_app.run(host=host, port=port, debug=debug)

def run_tests():
    unittest.main()

if __name__ == '__main__':
    if '--test' in sys.argv:
        sys.argv.remove('--test')
        run_tests()
    elif '--cli' in sys.argv:
        sys.argv.remove('--cli')
        run_cli()
    elif '--web' in sys.argv:
        run_web()
    else:
        run_web()
