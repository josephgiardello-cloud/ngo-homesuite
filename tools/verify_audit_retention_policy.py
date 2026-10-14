"""Validate audit governance without inventing approval or purging historical data."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def verify_policy(policy: dict, *, strict: bool) -> None:
    if policy.get("schema_version") != 1 or policy.get("status") not in {"pending_approval", "approved"}:
        raise ValueError("Invalid audit retention policy status/version")
    if policy.get("automatic_historical_deletion") is not False or policy.get("historical_rewrite_allowed") is not False:
        raise ValueError("Historical audit deletion/rewriting is not authorized")
    if policy.get("legal_hold_overrides_expiry") is not True:
        raise ValueError("Legal holds must override expiry")
    if policy.get("read_roles") != ["tenant_admin", "explicitly_authorized_compliance_operator"]:
        raise ValueError("Audit access policy does not permit staff/viewer/AI access")
    if strict or policy["status"] == "approved":
        if policy["status"] != "approved":
            raise ValueError("Organization/legal approval is required before production")
        for key in (
            "security_owner", "compliance_approver", "approval_record", "archive_and_deletion_runbook",
            "backup_expiry_and_restore_runbook", "audit_chain_boundary_preservation_runbook",
        ):
            if not isinstance(policy.get(key), str) or not policy[key].strip():
                raise ValueError(f"Approved audit policy requires {key}")
        for key in ("organization_id", "access_review_interval_days"):
            if type(policy.get(key)) is not int or policy[key] <= 0:
                raise ValueError(f"Approved audit policy requires positive {key}")
        periods = policy.get("retention_days")
        required = {"financial_audit", "security_audit", "legacy_sensitive_snapshots", "exports_and_backups"}
        if not isinstance(periods, dict) or set(periods) != required or any(
            type(value) is not int or value <= 0 for value in periods.values()
        ):
            raise ValueError("Every audit/backup class requires an approved positive retention period")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, default=Path("docs/audit_retention_policy.json"))
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    try:
        verify_policy(json.loads(args.path.read_text(encoding="utf-8")), strict=args.strict)
    except ValueError as exc:
        parser.exit(1, f"Audit governance gate failed: {exc}\n")
    print("Audit retention policy structure validated; approval is not inferred")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
