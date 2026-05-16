"""Grant approval workflow service with configurable chains and SoD enforcement."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select

from ngo_homesuite.db.utils import audit
from ngo_homesuite.models.core import GrantApprovalDecision, GrantApprovalRequest, db


class GrantApprovalError(ValueError):
    """Raised when grant action approval workflow constraints are violated."""


_VALID_APPROVAL_ACTIONS = {
    "proposal_submit",
    "disbursement_add",
    "outcome_record",
    "grant_closeout",
}

_DEFAULT_CHAIN_RULES = {
    "proposal_submit": {"required_approvals": 1, "approver_roles": {"finance", "org_admin", "executive"}},
    "disbursement_add": {"required_approvals": 2, "approver_roles": {"finance_admin", "controller", "org_admin", "executive"}},
    "outcome_record": {"required_approvals": 1, "approver_roles": {"program_admin", "org_admin", "executive", "compliance_officer"}},
    "grant_closeout": {"required_approvals": 2, "approver_roles": {"controller", "org_admin", "executive"}},
}


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _normalize_roles(roles: Optional[list[str]]) -> list[str]:
    if not roles:
        return []
    normalized = sorted({(item or "").strip().lower() for item in roles if (item or "").strip()})
    return normalized


def _get_approval_request(request_id: int, organization_id: int) -> GrantApprovalRequest:
    approval = db.session.scalars(
        select(GrantApprovalRequest).where(
            GrantApprovalRequest.id == request_id,
            GrantApprovalRequest.organization_id == organization_id,
        ).with_for_update().limit(1)
    ).first()
    if approval is None:
        raise GrantApprovalError("approval request not found for organization")
    return approval


def _expire_if_needed(approval: GrantApprovalRequest, now: datetime) -> None:
    if approval.expires_at and approval.expires_at <= now and approval.status in {"pending", "escalated"}:
        approval.status = "expired"
        db.session.commit()
        audit(
            "grant.approval.request.expired",
            entity_type="grant_approval_request",
            entity_id=int(approval.id),
            details={
                "organization_id": int(approval.organization_id),
                "action_type": approval.action_type,
                "resource_type": approval.resource_type,
                "resource_id": int(approval.resource_id),
                "expires_at": approval.expires_at.isoformat() if approval.expires_at else None,
                "required_approvals": int(approval.required_approvals or 1),
            },
        )


def _resolve_required_approvals(action_type: str, required_approvals: Optional[int]) -> int:
    default_value = int(_DEFAULT_CHAIN_RULES[action_type]["required_approvals"])
    resolved = int(required_approvals) if required_approvals is not None else default_value
    if resolved <= 0:
        raise GrantApprovalError("required_approvals must be positive")
    return resolved


def _resolve_allowed_roles(action_type: str, approver_roles: Optional[list[str]]) -> list[str]:
    default_roles = sorted(_DEFAULT_CHAIN_RULES[action_type]["approver_roles"])
    if approver_roles:
        normalized = _normalize_roles(approver_roles)
        if not normalized:
            raise GrantApprovalError("approver_roles must include at least one role")
        return normalized
    return default_roles


def create_approval_request(
    organization_id: int,
    *,
    action_type: str,
    resource_type: str,
    resource_id: int,
    requested_by_user_id: int,
    requested_by_role: str,
    payload: Optional[dict] = None,
    required_approvals: Optional[int] = None,
    approver_roles: Optional[list[str]] = None,
    expires_at: Optional[datetime] = None,
    expires_in_hours: Optional[int] = None,
    escalation_role: Optional[str] = None,
) -> GrantApprovalRequest:
    if action_type not in _VALID_APPROVAL_ACTIONS:
        raise GrantApprovalError(f"invalid approval action '{action_type}'")
    if not resource_type.strip():
        raise GrantApprovalError("resource_type is required")
    if int(resource_id) <= 0:
        raise GrantApprovalError("resource_id must be positive")

    now = _utcnow_naive()
    effective_expires_at = expires_at
    if effective_expires_at is None and expires_in_hours is not None:
        effective_expires_at = now + timedelta(hours=int(expires_in_hours))
    if effective_expires_at is not None and effective_expires_at <= now:
        raise GrantApprovalError("expires_at must be in the future")

    resolved_required_approvals = _resolve_required_approvals(action_type, required_approvals)
    resolved_approver_roles = _resolve_allowed_roles(action_type, approver_roles)

    approval = GrantApprovalRequest(
        organization_id=organization_id,
        action_type=action_type,
        resource_type=resource_type.strip(),
        resource_id=int(resource_id),
        requested_by_user_id=int(requested_by_user_id),
        requested_by_role=(requested_by_role or "").strip() or "unknown",
        status="pending",
        required_approvals=resolved_required_approvals,
        approver_roles_json=resolved_approver_roles,
        expires_at=effective_expires_at,
        escalation_role=(escalation_role or "").strip().lower() or None,
        payload_json=payload or None,
    )
    db.session.add(approval)
    db.session.commit()

    audit(
        "grant.approval.request.create",
        entity_type="grant_approval_request",
        entity_id=int(approval.id),
        details={
            "organization_id": int(organization_id),
            "after": {
                "action_type": approval.action_type,
                "resource_type": approval.resource_type,
                "resource_id": int(approval.resource_id),
                "status": approval.status,
                "requested_by_user_id": int(approval.requested_by_user_id),
                "requested_by_role": approval.requested_by_role,
                "required_approvals": int(approval.required_approvals),
                "approver_roles": approval.approver_roles_json or [],
                "expires_at": approval.expires_at.isoformat() if approval.expires_at else None,
                "escalation_role": approval.escalation_role,
                "payload": approval.payload_json,
            },
        },
    )
    return approval


def decide_approval_request(
    request_id: int,
    organization_id: int,
    *,
    decided_by_user_id: int,
    decided_by_role: str,
    decision: str,
    comment: Optional[str] = None,
    rationale: Optional[str] = None,
) -> GrantApprovalRequest:
    normalized_decision = (decision or "").strip().lower()
    if normalized_decision not in {"approved", "rejected"}:
        raise GrantApprovalError("decision must be approved or rejected")

    approval = _get_approval_request(request_id, organization_id)
    now = _utcnow_naive()
    _expire_if_needed(approval, now)
    if approval.status not in {"pending", "escalated"}:
        raise GrantApprovalError("approval request is no longer pending")

    if int(decided_by_user_id) == int(approval.requested_by_user_id):
        raise GrantApprovalError("requester cannot approve/reject their own request")

    normalized_role = (decided_by_role or "").strip().lower()
    allowed_roles = _normalize_roles(list(approval.approver_roles_json or []))
    if normalized_role not in allowed_roles:
        raise GrantApprovalError("approver role not allowed for this approval request")

    existing_by_user = db.session.scalars(
        select(GrantApprovalDecision).where(
            GrantApprovalDecision.request_id == approval.id,
            GrantApprovalDecision.organization_id == organization_id,
            GrantApprovalDecision.decided_by_user_id == int(decided_by_user_id),
        ).limit(1)
    ).first()
    if existing_by_user is not None:
        raise GrantApprovalError("approver has already decided this request")

    decision_row = GrantApprovalDecision(
        request_id=approval.id,
        organization_id=organization_id,
        decided_by_user_id=int(decided_by_user_id),
        decided_by_role=normalized_role,
        decision=normalized_decision,
        comment=(comment or "").strip() or None,
        rationale=(rationale or "").strip() or None,
    )
    db.session.add(decision_row)
    db.session.flush()

    if normalized_decision == "rejected":
        approval.status = "rejected"
    else:
        approved_count = int(
            db.session.scalar(
                select(func.count(GrantApprovalDecision.id)).where(
                    GrantApprovalDecision.request_id == approval.id,
                    GrantApprovalDecision.organization_id == organization_id,
                    GrantApprovalDecision.decision == "approved",
                )
            )
            or 0
        )
        approval.status = "approved" if approved_count >= int(approval.required_approvals or 1) else "pending"

    db.session.commit()

    audit(
        "grant.approval.request.decision",
        entity_type="grant_approval_request",
        entity_id=int(approval.id),
        details={
            "organization_id": int(organization_id),
            "action_type": approval.action_type,
            "resource_type": approval.resource_type,
            "resource_id": int(approval.resource_id),
            "decision": normalized_decision,
            "comment": decision_row.comment,
            "rationale": decision_row.rationale,
            "decided_by_user_id": int(decided_by_user_id),
            "decided_by_role": normalized_role,
            "required_approvals": int(approval.required_approvals or 1),
            "approved_decision_count": int(
                db.session.scalar(
                    select(func.count(GrantApprovalDecision.id)).where(
                        GrantApprovalDecision.request_id == approval.id,
                        GrantApprovalDecision.organization_id == organization_id,
                        GrantApprovalDecision.decision == "approved",
                    )
                )
                or 0
            ),
            "status": approval.status,
            "approver_roles": approval.approver_roles_json or [],
        },
    )
    return approval


def consume_approved_request(
    *,
    request_id: int,
    organization_id: int,
    required_action: str,
    required_resource_type: str,
    required_resource_id: int,
    executed_by_user_id: int,
) -> GrantApprovalRequest:
    approval = _get_approval_request(request_id, organization_id)
    _expire_if_needed(approval, _utcnow_naive())

    if approval.status != "approved":
        raise GrantApprovalError("approval request must be approved before execution")
    if approval.action_type != required_action:
        raise GrantApprovalError("approval action does not match requested operation")
    if approval.resource_type != required_resource_type or int(approval.resource_id) != int(required_resource_id):
        raise GrantApprovalError("approval resource mismatch")
    if int(executed_by_user_id) == int(approval.requested_by_user_id):
        raise GrantApprovalError("requester cannot execute their own approved request")

    prior_decider = db.session.scalars(
        select(GrantApprovalDecision).where(
            GrantApprovalDecision.request_id == approval.id,
            GrantApprovalDecision.organization_id == organization_id,
            GrantApprovalDecision.decided_by_user_id == int(executed_by_user_id),
        ).limit(1)
    ).first()
    if prior_decider is not None:
        raise GrantApprovalError("approver cannot execute the same request")

    approval.status = "executed"
    db.session.commit()

    audit(
        "grant.approval.request.execute",
        entity_type="grant_approval_request",
        entity_id=int(approval.id),
        details={
            "organization_id": int(organization_id),
            "action_type": approval.action_type,
            "resource_type": approval.resource_type,
            "resource_id": int(approval.resource_id),
            "required_approvals": int(approval.required_approvals or 1),
            "approved_decision_count": int(
                db.session.scalar(
                    select(func.count(GrantApprovalDecision.id)).where(
                        GrantApprovalDecision.request_id == approval.id,
                        GrantApprovalDecision.organization_id == organization_id,
                        GrantApprovalDecision.decision == "approved",
                    )
                )
                or 0
            ),
            "status": approval.status,
            "executed_by_user_id": int(executed_by_user_id),
            "requested_by_user_id": int(approval.requested_by_user_id),
        },
    )
    return approval


def escalate_expired_requests(
    organization_id: int,
    *,
    now: Optional[datetime] = None,
) -> list[GrantApprovalRequest]:
    effective_now = now or _utcnow_naive()
    candidates = list(
        db.session.scalars(
            select(GrantApprovalRequest).where(
                GrantApprovalRequest.organization_id == organization_id,
                GrantApprovalRequest.status == "pending",
                GrantApprovalRequest.expires_at.is_not(None),
                GrantApprovalRequest.expires_at <= effective_now,
            )
        )
    )

    escalated: list[GrantApprovalRequest] = []
    for approval in candidates:
        approval.status = "escalated"
        approval.escalated_at = effective_now
        if not approval.escalation_role:
            approval.escalation_role = "org_admin"
        escalated.append(approval)

    if not escalated:
        return []

    db.session.commit()

    for approval in escalated:
        audit(
            "grant.approval.request.escalated",
            entity_type="grant_approval_request",
            entity_id=int(approval.id),
            details={
                "organization_id": int(organization_id),
                "action_type": approval.action_type,
                "resource_type": approval.resource_type,
                "resource_id": int(approval.resource_id),
                "required_approvals": int(approval.required_approvals or 1),
                "approved_decision_count": int(
                    db.session.scalar(
                        select(func.count(GrantApprovalDecision.id)).where(
                            GrantApprovalDecision.request_id == approval.id,
                            GrantApprovalDecision.organization_id == organization_id,
                            GrantApprovalDecision.decision == "approved",
                        )
                    )
                    or 0
                ),
                "escalation_role": approval.escalation_role,
                "expires_at": approval.expires_at.isoformat() if approval.expires_at else None,
                "escalated_at": approval.escalated_at.isoformat() if approval.escalated_at else None,
                "status": approval.status,
            },
        )

    return escalated
