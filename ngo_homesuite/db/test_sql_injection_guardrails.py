from __future__ import annotations

import ast
from pathlib import Path


# Files that use dynamic SQL in migration plumbing with controlled internal identifiers.
# These are reviewed separately and intentionally excluded from this broad guardrail.
SAFE_DYNAMIC_SQL_ALLOWLIST = {
    "ngo_homesuite/db/migrate.py",
    "ngo_homesuite/db/connection.py",
    "ngo_homesuite/db/schema.py",
    "ngo_homesuite/utils/export.py",
    "ngo_homesuite/utils/import_export.py",
    "ngo_homesuite/utils/integrity_drift.py",
    "ngo_homesuite/services/activity_timeline_service.py",  # Uses text() with literal templates + parameterized queries
    "ngo_homesuite/services/campaign_service.py",  # Uses controlled SQLAlchemy text fragments with bound parameters
    "ngo_homesuite/services/campaign_email_service.py",  # Uses SQLAlchemy select() constructs and bound values
    "ngo_homesuite/services/reminder_service.py",  # Uses select() with SQLAlchemy parameterized queries
    "ngo_homesuite/utils/email_worker.py",  # Queue processing SQL reviewed for fixed templates and parameter binding
    "ngo_homesuite/web/integrations_routes.py",  # Integration settings queries use parameterized SQLAlchemy execution
    "ngo_homesuite/web/test_integrations_routes.py",  # Test-only SQL execution patterns
    "ngo_homesuite/services/reporting_service.py",  # Uses SQLAlchemy select() constructs with bound parameters only
    "ngo_homesuite/services/campaign_projection_service.py",  # Uses SQLAlchemy select() ORM constructs with bound parameters only
    "ngo_homesuite/web/events_routes.py",  # Event route queries use reviewed SQLAlchemy execution patterns
    "ngo_homesuite/web/test_event_management_routes.py",  # Test-only SQL setup/verification helpers
    "ngo_homesuite/web/test_donor_delete_routes.py",  # Test-only SQL setup/verification helpers
}


ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = [
    ROOT / "ngo_homesuite" / "db",
    ROOT / "ngo_homesuite" / "utils",
    ROOT / "ngo_homesuite" / "services",
    ROOT / "ngo_homesuite" / "web",
]


DANGEROUS_FIRST_ARG_NODES = (
    ast.JoinedStr,  # f"..."
    ast.BinOp,      # "..." + user_input
    ast.Call,       # format(...)
)



def _is_execute_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr in {"execute", "executemany"}
    return False



def _scan_file(path: Path) -> list[str]:
    rel = path.relative_to(ROOT).as_posix()
    if rel in SAFE_DYNAMIC_SQL_ALLOWLIST:
        return []

    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    findings: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not _is_execute_call(node):
            continue
        if not node.args:
            continue

        first = node.args[0]

        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            continue

        if isinstance(first, DANGEROUS_FIRST_ARG_NODES):
            findings.append(f"{rel}:{node.lineno}")

    return findings



def test_execute_calls_use_literal_sql_strings_or_allowlisted_files() -> None:
    py_files: list[Path] = []
    for base in SCAN_DIRS:
        py_files.extend(base.rglob("*.py"))

    violations: list[str] = []
    for file_path in py_files:
        violations.extend(_scan_file(file_path))

    assert not violations, (
        "Potential dynamic SQL execution sites detected. "
        "Use parameterized SQL with literal query templates and bound parameters. "
        f"Review these locations: {violations}"
    )
