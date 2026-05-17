import json
from datetime import datetime
from pathlib import Path
from typing import Any


CONTROL_CATALOG: list[dict[str, str]] = [
    {"id": "GOV-001", "domain": "governance", "severity": "high", "requirement": "Conflict of interest policy with annual attestations"},
    {"id": "GOV-002", "domain": "governance", "severity": "high", "requirement": "Board independence and related-party oversight"},
    {"id": "FIL-001", "domain": "filings", "severity": "high", "requirement": "Federal and state filing calendar with due dates"},
    {"id": "FIL-002", "domain": "filings", "severity": "high", "requirement": "Late filing escalation workflow"},
    {"id": "FID-001", "domain": "fiduciary", "severity": "high", "requirement": "Restricted funds tracking and spend controls"},
    {"id": "FID-002", "domain": "fiduciary", "severity": "high", "requirement": "Grant condition monitoring and evidence"},
    {"id": "AML-001", "domain": "third_party", "severity": "high", "requirement": "Sanctions/AML checks for partners and beneficiaries"},
    {"id": "DUE-001", "domain": "third_party", "severity": "medium", "requirement": "Vendor/partner due diligence with renewal checks"},
    {"id": "POL-001", "domain": "policy", "severity": "medium", "requirement": "Policy versioning and attestation lifecycle"},
    {"id": "INC-001", "domain": "incident", "severity": "medium", "requirement": "Incident intake, investigation, and remediation"},
    {"id": "ACC-001", "domain": "access", "severity": "high", "requirement": "Role-based access control and action-level audit trail"},
]

DEFAULT_BASELINE_FILE = Path(__file__).resolve().parent.parent / "baselines" / "external_compliance_controls.json"


def _load_baseline(baseline_file: str | None = None) -> dict[str, Any]:
    target = Path(baseline_file) if baseline_file else DEFAULT_BASELINE_FILE
    if not target.exists():
        return {"sources": [], "control_baseline": []}
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return {"sources": [], "control_baseline": []}


def _is_present(section: dict[str, Any], keys: list[str]) -> bool:
    for key in keys:
        value = section.get(key)
        if isinstance(value, bool) and value:
            return True
        if isinstance(value, (list, dict, str)) and value:
            return True
    return False


def evaluate(profile: dict[str, Any], baseline_file: str | None = None) -> dict[str, Any]:
    baseline = _load_baseline(baseline_file)
    baseline_controls = {
        row.get("control_id"): row for row in baseline.get("control_baseline", []) if row.get("control_id")
    }

    checks: dict[str, bool] = {
        "GOV-001": _is_present(profile.get("governance", {}), ["conflict_policy", "annual_attestations"]),
        "GOV-002": _is_present(profile.get("governance", {}), ["board_independence", "related_party_review"]),
        "FIL-001": _is_present(profile.get("filings", {}), ["calendar", "due_dates"]),
        "FIL-002": _is_present(profile.get("filings", {}), ["late_escalation", "overdue_workflow"]),
        "FID-001": _is_present(profile.get("fiduciary", {}), ["restricted_funds", "restriction_checks"]),
        "FID-002": _is_present(profile.get("fiduciary", {}), ["grant_conditions", "deliverable_tracking"]),
        "AML-001": _is_present(profile.get("third_party", {}), ["sanctions_screening", "aml_checks"]),
        "DUE-001": _is_present(profile.get("third_party", {}), ["vendor_due_diligence", "renewal_checks"]),
        "POL-001": _is_present(profile.get("policy", {}), ["policy_versions", "attestations"]),
        "INC-001": _is_present(profile.get("incident", {}), ["incident_workflow", "remediation_tracking"]),
        "ACC-001": _is_present(profile.get("access", {}), ["rbac", "audit_log"]),
    }

    results: list[dict[str, Any]] = []
    severity_totals = {"high": 0, "medium": 0, "low": 0}
    severity_missing = {"high": 0, "medium": 0, "low": 0}

    for control in CONTROL_CATALOG:
        status = "met" if checks.get(control["id"], False) else "missing"
        severity = control["severity"]
        external_meta = baseline_controls.get(control["id"], {})
        severity_totals[severity] += 1
        if status == "missing":
            severity_missing[severity] += 1
        results.append(
            {
                **control,
                "status": status,
                "required": bool(external_meta.get("required", True)),
                "evidence_standard": external_meta.get("evidence_standard"),
                "source_url": external_meta.get("source_url"),
            }
        )

    total_controls = len(results)
    met_controls = sum(1 for r in results if r["status"] == "met")
    score = round((met_controls / total_controls) * 100, 1) if total_controls else 0.0

    domain_summary: dict[str, dict[str, int]] = {}
    for row in results:
        domain = row["domain"]
        domain_summary.setdefault(domain, {"met": 0, "missing": 0})
        domain_summary[domain][row["status"]] += 1

    return {
        "generated_at": datetime.now().isoformat(),
        "overall_score": score,
        "controls_total": total_controls,
        "controls_met": met_controls,
        "severity_totals": severity_totals,
        "severity_missing": severity_missing,
        "domain_summary": domain_summary,
        "controls": results,
        "priority_gaps": [row for row in results if row["status"] == "missing" and row["severity"] == "high"],
        "baseline_sources": baseline.get("sources", []),
    }


def run(input_file: str, out_file: str, baseline_file: str | None = None) -> dict[str, Any]:
    source = Path(input_file)
    profile = json.loads(source.read_text(encoding="utf-8"))
    result = evaluate(profile, baseline_file=baseline_file)
    out = Path(out_file)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result
