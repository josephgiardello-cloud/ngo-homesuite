from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask

from ngo_homesuite.models.core import Donation, Donor, Expense, Fund, Organization, Project, User, db


def _sqlite_path_from_uri(uri: str) -> str:
    val = str(uri or "")
    if val.startswith("sqlite:///"):
        return val.replace("sqlite:///", "", 1)
    return "ngo_homesuite.db"


def _verify_audit_chain(db_path: str, limit: int = 2000) -> dict[str, Any]:
    path = Path(db_path)
    if not path.exists():
        return {"present": False, "valid": False, "checked": 0, "error": "audit database file not found"}

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        table_exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='audit_log'"
        ).fetchone()
        if not table_exists:
            return {"present": False, "valid": False, "checked": 0, "error": "audit_log table not found"}

        rows = conn.execute(
            "SELECT id, timestamp_utc, actor, action, entity, metadata, hash_prev, hash_event "
            "FROM audit_log ORDER BY id ASC LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()

        prev_hash = None
        checked = 0
        for row in rows:
            event_str = (
                f"{row['timestamp_utc']}|{row['actor']}|{row['action']}|{row['entity']}|"
                f"{row['metadata'] or '{}'}|{row['hash_prev'] or ''}"
            )
            computed = hashlib.sha256(event_str.encode("utf-8")).hexdigest()
            if row["hash_prev"] != prev_hash:
                return {
                    "present": True,
                    "valid": False,
                    "checked": checked,
                    "broken_at_id": row["id"],
                    "error": "hash_prev chain mismatch",
                }
            if computed != row["hash_event"]:
                return {
                    "present": True,
                    "valid": False,
                    "checked": checked,
                    "broken_at_id": row["id"],
                    "error": "hash_event mismatch",
                }
            prev_hash = row["hash_event"]
            checked += 1

        return {
            "present": True,
            "valid": True,
            "checked": checked,
            "last_hash": prev_hash,
        }
    finally:
        conn.close()


def _recent_audit_events(db_path: str, limit: int = 20) -> list[dict[str, Any]]:
    path = Path(db_path)
    if not path.exists():
        return []

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        table_exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='audit_log'"
        ).fetchone()
        if not table_exists:
            return []

        rows = conn.execute(
            "SELECT id, timestamp_utc, actor, action, entity, hash_event "
            "FROM audit_log ORDER BY id DESC LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()

        return [
            {
                "id": int(row["id"]),
                "timestamp_utc": str(row["timestamp_utc"]),
                "actor": str(row["actor"]),
                "action": str(row["action"]),
                "entity": str(row["entity"]),
                "hash_event": str(row["hash_event"]),
            }
            for row in rows
        ]
    finally:
        conn.close()


def build_compliance_evidence(app: Flask, organization_id: int | None = None) -> dict[str, Any]:
    db_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", "sqlite:///ngo_homesuite.db"))
    db_path = _sqlite_path_from_uri(db_uri)

    if organization_id is None:
        organization_count = Organization.query.count()
        donor_count = Donor.query.count()
        donation_count = Donation.query.count()
        expense_count = Expense.query.count()
        project_count = Project.query.count()
        fund_count = Fund.query.count()
    else:
        organization_count = Organization.query.filter_by(id=organization_id).count()
        donor_count = Donor.query.filter_by(organization_id=organization_id).count()
        donation_count = Donation.query.filter_by(organization_id=organization_id).count()
        expense_count = Expense.query.filter_by(organization_id=organization_id).count()
        project_count = Project.query.filter_by(organization_id=organization_id).count()
        fund_count = Fund.query.filter_by(organization_id=organization_id).count()

    total_donations = (
        db.session.query(db.func.sum(Donation.amount))
        .filter_by(organization_id=organization_id)
        .scalar()
        if organization_id is not None
        else db.session.query(db.func.sum(Donation.amount)).scalar()
    ) or 0
    total_expenses = (
        db.session.query(db.func.sum(Expense.amount))
        .filter_by(organization_id=organization_id)
        .scalar()
        if organization_id is not None
        else db.session.query(db.func.sum(Expense.amount)).scalar()
    ) or 0

    controls = {
        "csrf_enabled": bool(app.config.get("WTF_CSRF_ENABLED", False)),
        "session_cookie_httponly": bool(app.config.get("SESSION_COOKIE_HTTPONLY", False)),
        "session_cookie_secure": bool(app.config.get("SESSION_COOKIE_SECURE", False)),
        "session_cookie_samesite": str(app.config.get("SESSION_COOKIE_SAMESITE", "")),
        "copilot_enabled": bool(app.config.get("COPILOT_ENABLED", False)),
        "copilot_web_tools_enabled": bool(app.config.get("COPILOT_ALLOW_WEB_TOOLS", False)),
        "rate_limit_enabled": bool(app.config.get("RATELIMIT_ENABLED", False)),
        "db_uri_scheme": "sqlite" if db_uri.startswith("sqlite") else "other",
    }

    audit_chain = _verify_audit_chain(db_path)
    evidence = {
        "schema": "ngohs-compliance-evidence-v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "organization_scope": organization_id,
        "controls": controls,
        "security_posture": {
            "immutable_audit_chain_valid": bool(audit_chain.get("valid", False)),
            "audit_chain": audit_chain,
            "recent_audit_events": _recent_audit_events(db_path, limit=20),
        },
        "data_inventory": {
            "organizations": organization_count,
            "users": User.query.count(),
            "donors": donor_count,
            "donations": donation_count,
            "expenses": expense_count,
            "projects": project_count,
            "funds": fund_count,
        },
        "financial_snapshot": {
            "total_donations": float(total_donations),
            "total_expenses": float(total_expenses),
            "net": float(total_donations - total_expenses),
        },
    }
    payload = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
    evidence["sha256"] = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return evidence
