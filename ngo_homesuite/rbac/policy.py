from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    ORG_ADMIN = "org_admin"
    CASE_WORKER = "case_worker"
    VOLUNTEER = "volunteer"
    AUDITOR = "auditor"


_ALLOWED_TRANSITIONS: dict[Role, set[str]] = {
    Role.ORG_ADMIN: {"*"},
    Role.CASE_WORKER: {
        "intake_submit",
        "intake_verify",
        "intake_assign",
        "case_resolve",
        "case_close",
        "donation_receipt",
        "donation_allocate",
        "donation_report",
    },
    Role.VOLUNTEER: {"service_delivery_record"},
    Role.AUDITOR: {"workflow_audit_replay"},
}


def can_transition_workflow(role: str, event_type: str) -> bool:
    try:
        parsed_role = Role(role)
    except ValueError:
        return False

    allowed = _ALLOWED_TRANSITIONS.get(parsed_role, set())
    return "*" in allowed or event_type in allowed
