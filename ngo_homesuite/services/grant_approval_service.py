"""Grant approval workflow service with configurable chains and SoD enforcement."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select

from ngo_homesuite.db.utils import audit
from ngo_homesuite.models.core import (
    GrantApprovalChainConfig,
    GrantApprovalDecision,
    GrantApprovalRequest,
    db,
)


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


def _extract_payload_amount(payload: Optional[dict]) -> Optional[float]:
    if not isinstance(payload, dict):
        return None
    candidate = payload.get("amount")
    if candidate is None:
        return None
    try:
        return float(candidate)
    except (TypeError, ValueError):
        return None


def list_chain_configs(organization_id: int, *, action_type: Optional[str] = None) -> list[GrantApprovalChainConfig]:
    stmt = select(GrantApprovalChainConfig).where(
        GrantApprovalChainConfig.organization_id == organization_id,
    )
    if action_type:
        stmt = stmt.where(GrantApprovalChainConfig.action_type == action_type)
    stmt = stmt.order_by(GrantApprovalChainConfig.priority.asc(), GrantApprovalChainConfig.id.asc())
    return list(db.session.scalars(stmt))


def upsert_chain_config(
    organization_id: int,
    *,
    action_type: str,
    approver_roles: list[str],
    required_approvals: int,
    min_amount: Optional[float] = None,
    max_amount: Optional[float] = None,
    escalation_role: Optional[str] = None,
    sla_hours: int = 72,
    escalation_sla_hours: int = 24,
    priority: int = 100,
    is_active: bool = True,
    config_id: Optional[int] = None,
) -> GrantApprovalChainConfig:
    if action_type not in _VALID_APPROVAL_ACTIONS:
        raise GrantApprovalError(f"invalid approval action '{action_type}'")
    normalized_roles = _normalize_roles(approver_roles)
    if not normalized_roles:
        raise GrantApprovalError("approver_roles must include at least one role")
    if int(required_approvals) <= 0:
        raise GrantApprovalError("required_approvals must be positive")
    if int(sla_hours) <= 0:
        raise GrantApprovalError("sla_hours must be positive")
    if int(escalation_sla_hours) <= 0:
        raise GrantApprovalError("escalation_sla_hours must be positive")
    if min_amount is not None and max_amount is not None and float(min_amount) > float(max_amount):
        raise GrantApprovalError("min_amount must be less than or equal to max_amount")

    if config_id is None:
        config = GrantApprovalChainConfig(organization_id=organization_id, action_type=action_type)
        db.session.add(config)
        event_action = "grant.approval.chain_config.create"
    else:
        config = db.session.scalars(
            select(GrantApprovalChainConfig).where(
                GrantApprovalChainConfig.id == config_id,
                GrantApprovalChainConfig.organization_id == organization_id,
            ).with_for_update().limit(1)
        ).first()
        if config is None:
            raise GrantApprovalError("approval chain config not found for organization")
        event_action = "grant.approval.chain_config.update"

    config.action_type = action_type
    config.min_amount = float(min_amount) if min_amount is not None else None
    config.max_amount = float(max_amount) if max_amount is not None else None
    config.required_approvals = int(required_approvals)
    config.approver_roles_json = normalized_roles
    config.escalation_role = (escalation_role or "").strip().lower() or None
    config.sla_hours = int(sla_hours)
    config.escalation_sla_hours = int(escalation_sla_hours)
    config.priority = int(priority)
    config.is_active = bool(is_active)
    db.session.commit()

    audit(
        event_action,
        entity_type="grant_approval_chain_config",
        entity_id=int(config.id),
        details={
            "organization_id": int(organization_id),
            "action_type": config.action_type,
            "min_amount": config.min_amount,
            "max_amount": config.max_amount,
            "required_approvals": int(config.required_approvals),
            "approver_roles": config.approver_roles_json,
            "escalation_role": config.escalation_role,
            "sla_hours": int(config.sla_hours),
            "escalation_sla_hours": int(config.escalation_sla_hours),
            "priority": int(config.priority),
            "is_active": bool(config.is_active),
        },
    )
    return config


def disable_chain_config(config_id: int, organization_id: int) -> GrantApprovalChainConfig:
    config = db.session.scalars(
        select(GrantApprovalChainConfig).where(
            GrantApprovalChainConfig.id == config_id,
            GrantApprovalChainConfig.organization_id == organization_id,
        ).with_for_update().limit(1)
    ).first()
    if config is None:
        raise GrantApprovalError("approval chain config not found for organization")
    config.is_active = False
    db.session.commit()
    audit(
        "grant.approval.chain_config.disable",
        entity_type="grant_approval_chain_config",
        entity_id=int(config.id),
        details={
            "organization_id": int(organization_id),
            "action_type": config.action_type,
            "priority": int(config.priority),
        },
    )
    return config


def _resolve_chain_config(organization_id: int, action_type: str, payload: Optional[dict]) -> Optional[GrantApprovalChainConfig]:
    amount = _extract_payload_amount(payload)
    configs = list_chain_configs(organization_id, action_type=action_type)
    for config in configs:
        if not config.is_active:
            continue
        if amount is not None:
            if config.min_amount is not None and amount < float(config.min_amount):
                continue
            if config.max_amount is not None and amount > float(config.max_amount):
                continue
        elif config.min_amount is not None or config.max_amount is not None:
            continue
        return config
    return None


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
    if not approval.expires_at or approval.expires_at > now:
        return

    if approval.status == "pending":
        escalation_role = (approval.escalation_role or "").strip().lower() or "org_admin"
        escalation_hours = int((approval.payload_json or {}).get("escalation_sla_hours", 24))
        approval.status = "escalated"
        approval.escalated_at = now
        approval.escalation_role = escalation_role
        approval.expires_at = now + timedelta(hours=escalation_hours)
        db.session.commit()
        audit(
            "grant.approval.request.escalated",
            entity_type="grant_approval_request",
            entity_id=int(approval.id),
            details={
                "organization_id": int(approval.organization_id),
                "action_type": approval.action_type,
                "resource_type": approval.resource_type,
                "resource_id": int(approval.resource_id),
                "required_approvals": int(approval.required_approvals or 1),
                "escalation_role": approval.escalation_role,
                "escalated_at": approval.escalated_at.isoformat() if approval.escalated_at else None,
                "next_expires_at": approval.expires_at.isoformat() if approval.expires_at else None,
                "status": approval.status,
            },
        )
        return

    if approval.status == "escalated":
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
                "status": approval.status,
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


def _resolve_request_policy(
    organization_id: int,
    action_type: str,
    payload: Optional[dict],
    *,
    required_approvals: Optional[int],
    approver_roles: Optional[list[str]],
    escalation_role: Optional[str],
    expires_at: Optional[datetime],
    expires_in_hours: Optional[int],
) -> tuple[int, list[str], Optional[str], Optional[datetime], dict]:
    config = _resolve_chain_config(organization_id, action_type, payload)
    metadata: dict = {}
    if config is not None:
        metadata["chain_config_id"] = int(config.id)
        metadata["escalation_sla_hours"] = int(config.escalation_sla_hours)

    resolved_required_approvals = _resolve_required_approvals(
        action_type,
        required_approvals if required_approvals is not None else (int(config.required_approvals) if config else None),
    )
    resolved_approver_roles = _resolve_allowed_roles(
        action_type,
        approver_roles if approver_roles else (list(config.approver_roles_json or []) if config else None),
    )

    resolved_escalation_role = (escalation_role or "").strip().lower() or None
    if resolved_escalation_role is None and config and config.escalation_role:
        resolved_escalation_role = str(config.escalation_role).strip().lower() or None

    now = _utcnow_naive()
    resolved_expires_at = expires_at
    if resolved_expires_at is None and expires_in_hours is not None:
        resolved_expires_at = now + timedelta(hours=int(expires_in_hours))
    if resolved_expires_at is None and config:
        resolved_expires_at = now + timedelta(hours=int(config.sla_hours))

    if resolved_expires_at is not None and resolved_expires_at <= now:
        raise GrantApprovalError("expires_at must be in the future")

    return (
        resolved_required_approvals,
        resolved_approver_roles,
        resolved_escalation_role,
        resolved_expires_at,
        metadata,
    )


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

    (
        resolved_required_approvals,
        resolved_approver_roles,
        resolved_escalation_role,
        effective_expires_at,
        policy_metadata,
    ) = _resolve_request_policy(
        organization_id,
        action_type,
        payload,
        required_approvals=required_approvals,
        approver_roles=approver_roles,
        escalation_role=escalation_role,
        expires_at=expires_at,
        expires_in_hours=expires_in_hours,
    )

    effective_payload = dict(payload or {})
    effective_payload.update(policy_metadata)

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
        escalation_role=resolved_escalation_role,
        payload_json=effective_payload or None,
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
                "chain_config_id": (approval.payload_json or {}).get("chain_config_id"),
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
    if approval.status == "escalated" and approval.escalation_role:
        allowed_roles = _normalize_roles(allowed_roles + [approval.escalation_role])
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
    candidate_ids = list(
        db.session.scalars(
            select(GrantApprovalRequest.id).where(
                GrantApprovalRequest.organization_id == organization_id,
                GrantApprovalRequest.status.in_(["pending", "escalated"]),
                GrantApprovalRequest.expires_at.is_not(None),
                GrantApprovalRequest.expires_at <= effective_now,
            )
        )
    )

    escalated: list[GrantApprovalRequest] = []
    for request_id in candidate_ids:
        approval = _get_approval_request(int(request_id), organization_id)
        previous = approval.status
        _expire_if_needed(approval, effective_now)
        if previous != "escalated" and approval.status == "escalated":
            escalated.append(approval)

    return escalated


def process_escalation_sla_queue(
    *,
    organization_id: Optional[int] = None,
    now: Optional[datetime] = None,
) -> dict:
    effective_now = now or _utcnow_naive()
    stmt = select(GrantApprovalRequest.organization_id).where(
        GrantApprovalRequest.status.in_(["pending", "escalated"]),
        GrantApprovalRequest.expires_at.is_not(None),
        GrantApprovalRequest.expires_at <= effective_now,
    ).distinct()
    if organization_id is not None:
        stmt = stmt.where(GrantApprovalRequest.organization_id == organization_id)

    org_ids = [int(value) for value in db.session.scalars(stmt)]
    escalated_count = 0
    expired_count = 0

    for org_id in org_ids:
        candidate_ids = list(
            db.session.scalars(
                select(GrantApprovalRequest.id).where(
                    GrantApprovalRequest.organization_id == org_id,
                    GrantApprovalRequest.status.in_(["pending", "escalated"]),
                    GrantApprovalRequest.expires_at.is_not(None),
                    GrantApprovalRequest.expires_at <= effective_now,
                )
            )
        )

        for request_id in candidate_ids:
            approval = _get_approval_request(int(request_id), org_id)
            before = approval.status
            _expire_if_needed(approval, effective_now)
            after = approval.status
            if before != "escalated" and after == "escalated":
                escalated_count += 1
            if before != "expired" and after == "expired":
                expired_count += 1

    audit(
        "grant.approval.sla_queue.process",
        entity_type="grant_approval_request",
        entity_id=0,
        details={
            "organization_id": int(organization_id) if organization_id is not None else None,
            "processed_org_count": len(org_ids),
            "escalated_count": escalated_count,
            "expired_count": expired_count,
            "processed_at": effective_now.isoformat(),
        },
    )

    return {
        "processed_org_count": len(org_ids),
        "escalated_count": escalated_count,
        "expired_count": expired_count,
    }
