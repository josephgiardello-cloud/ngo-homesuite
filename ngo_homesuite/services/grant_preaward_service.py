from __future__ import annotations

import os
import warnings
from datetime import date
from typing import Optional

from sqlalchemy import func, select

from ngo_homesuite.db.utils import audit
from ngo_homesuite.models.core import Grant, GrantOpportunity, GrantProposal, db


if os.getenv("NGOHS_WARN_DIRECT_GRANT_SERVICE_IMPORTS", "0") == "1":
    warnings.warn(
        "Direct import of grant_preaward_service is deprecated. Use ngo_homesuite.grants.facade.GrantsFacade instead.",
        DeprecationWarning,
        stacklevel=2,
    )


_VALID_OPPORTUNITY_STATUSES = {"identified", "qualified", "in_progress", "submitted", "awarded", "declined", "archived"}
_VALID_PROPOSAL_OUTCOMES = {"draft", "submitted", "awarded", "declined", "withdrawn"}


def _compute_probability_weighted_amount(amount_min: Optional[float], amount_max: Optional[float], probability: float) -> float:
    if amount_min is None and amount_max is None:
        return 0.0
    if amount_min is None:
        base = float(amount_max or 0)
    elif amount_max is None:
        base = float(amount_min or 0)
    else:
        base = (float(amount_min) + float(amount_max)) / 2.0
    return round(base * float(probability), 2)


def _validate_probability(probability: float) -> float:
    value = float(probability)
    if value < 0 or value > 1:
        raise ValueError("probability must be between 0 and 1")
    return value


def create_opportunity(
    organization_id: int,
    *,
    funder_name: str,
    program_name: str,
    title: str,
    deadline: Optional[date] = None,
    amount_min: Optional[float] = None,
    amount_max: Optional[float] = None,
    probability: float = 0.0,
    status: str = "identified",
    notes: Optional[str] = None,
) -> GrantOpportunity:
    if not funder_name.strip():
        raise ValueError("funder_name is required")
    if not program_name.strip():
        raise ValueError("program_name is required")
    if not title.strip():
        raise ValueError("title is required")
    if status not in _VALID_OPPORTUNITY_STATUSES:
        raise ValueError(f"invalid opportunity status '{status}'")
    if amount_min is not None and float(amount_min) < 0:
        raise ValueError("amount_min cannot be negative")
    if amount_max is not None and float(amount_max) < 0:
        raise ValueError("amount_max cannot be negative")
    if amount_min is not None and amount_max is not None and float(amount_max) < float(amount_min):
        raise ValueError("amount_max must be greater than or equal to amount_min")

    probability_value = _validate_probability(probability)
    weighted = _compute_probability_weighted_amount(amount_min, amount_max, probability_value)

    opportunity = GrantOpportunity(
        organization_id=organization_id,
        funder_name=funder_name.strip(),
        program_name=program_name.strip(),
        title=title.strip(),
        deadline=deadline,
        amount_min=float(amount_min) if amount_min is not None else None,
        amount_max=float(amount_max) if amount_max is not None else None,
        probability=probability_value,
        probability_weighted_amount=weighted,
        status=status,
        notes=(notes or "").strip() or None,
    )
    db.session.add(opportunity)
    db.session.commit()
    audit(
        "grant.opportunity.create",
        entity_type="grant_opportunity",
        entity_id=int(opportunity.id),
        details={
            "organization_id": int(organization_id),
            "after": {
                "status": opportunity.status,
                "probability": float(opportunity.probability),
                "probability_weighted_amount": float(opportunity.probability_weighted_amount),
            },
        },
    )
    return opportunity


def list_opportunities(organization_id: int, *, status: Optional[str] = None) -> list[GrantOpportunity]:
    stmt = select(GrantOpportunity).where(GrantOpportunity.organization_id == organization_id)
    if status:
        stmt = stmt.where(GrantOpportunity.status == status)
    stmt = stmt.order_by(GrantOpportunity.deadline.asc().nullslast(), GrantOpportunity.created_at.desc())
    return list(db.session.scalars(stmt))


def update_opportunity(opportunity_id: int, organization_id: int, **fields) -> GrantOpportunity:
    opportunity = db.session.scalars(
        select(GrantOpportunity).where(
            GrantOpportunity.id == opportunity_id,
            GrantOpportunity.organization_id == organization_id,
        ).limit(1)
    ).first()
    if opportunity is None:
        raise LookupError("opportunity not found for organization")

    before = {
        "status": opportunity.status,
        "probability": float(opportunity.probability or 0),
        "amount_min": float(opportunity.amount_min) if opportunity.amount_min is not None else None,
        "amount_max": float(opportunity.amount_max) if opportunity.amount_max is not None else None,
        "version_id": int(opportunity.version_id),
    }

    expected_version = fields.get("expected_version")
    if expected_version is not None and int(expected_version) != int(opportunity.version_id):
        raise ValueError(f"opportunity version mismatch (expected {expected_version}, current {opportunity.version_id})")

    if "status" in fields and fields["status"] not in _VALID_OPPORTUNITY_STATUSES:
        raise ValueError(f"invalid opportunity status '{fields['status']}'")

    for key in ["funder_name", "program_name", "title", "deadline", "status"]:
        if key in fields:
            value = fields[key]
            if key in {"funder_name", "program_name", "title"}:
                clean = str(value or "").strip()
                if not clean:
                    raise ValueError(f"{key} is required")
                setattr(opportunity, key, clean)
            else:
                setattr(opportunity, key, value)

    if "amount_min" in fields:
        opportunity.amount_min = float(fields["amount_min"]) if fields["amount_min"] is not None else None
    if "amount_max" in fields:
        opportunity.amount_max = float(fields["amount_max"]) if fields["amount_max"] is not None else None
    if opportunity.amount_min is not None and opportunity.amount_max is not None and float(opportunity.amount_max) < float(opportunity.amount_min):
        raise ValueError("amount_max must be greater than or equal to amount_min")

    if "probability" in fields:
        opportunity.probability = _validate_probability(float(fields["probability"]))

    if "notes" in fields:
        opportunity.notes = str(fields["notes"] or "").strip() or None

    opportunity.probability_weighted_amount = _compute_probability_weighted_amount(
        opportunity.amount_min,
        opportunity.amount_max,
        float(opportunity.probability or 0),
    )

    db.session.commit()
    audit(
        "grant.opportunity.update",
        entity_type="grant_opportunity",
        entity_id=int(opportunity.id),
        details={
            "organization_id": int(organization_id),
            "before": before,
            "after": {
                "status": opportunity.status,
                "probability": float(opportunity.probability or 0),
                "amount_min": float(opportunity.amount_min) if opportunity.amount_min is not None else None,
                "amount_max": float(opportunity.amount_max) if opportunity.amount_max is not None else None,
                "version_id": int(opportunity.version_id),
            },
        },
    )
    return opportunity


def create_proposal(
    opportunity_id: int,
    organization_id: int,
    *,
    amount_requested: Optional[float] = None,
    narrative_summary: Optional[str] = None,
    document_ref: Optional[str] = None,
    notes: Optional[str] = None,
) -> GrantProposal:
    opportunity = db.session.scalars(
        select(GrantOpportunity).where(
            GrantOpportunity.id == opportunity_id,
            GrantOpportunity.organization_id == organization_id,
        ).limit(1)
    ).first()
    if opportunity is None:
        raise LookupError("opportunity not found for organization")

    if amount_requested is not None and float(amount_requested) < 0:
        raise ValueError("amount_requested cannot be negative")

    next_version = int(
        db.session.scalar(
            select(func.coalesce(func.max(GrantProposal.version_number), 0)).where(
                GrantProposal.opportunity_id == opportunity_id,
                GrantProposal.organization_id == organization_id,
            )
        )
        or 0
    ) + 1

    proposal = GrantProposal(
        opportunity_id=opportunity_id,
        organization_id=organization_id,
        version_number=next_version,
        amount_requested=float(amount_requested) if amount_requested is not None else None,
        narrative_summary=(narrative_summary or "").strip() or None,
        document_ref=(document_ref or "").strip() or None,
        notes=(notes or "").strip() or None,
        outcome="draft",
    )
    db.session.add(proposal)
    db.session.commit()
    audit(
        "grant.proposal.create",
        entity_type="grant_proposal",
        entity_id=int(proposal.id),
        details={
            "organization_id": int(organization_id),
            "after": {
                "opportunity_id": int(opportunity_id),
                "version_number": int(proposal.version_number),
                "outcome": proposal.outcome,
            },
        },
    )
    return proposal


def submit_proposal(
    proposal_id: int,
    organization_id: int,
    *,
    submission_date: date,
    document_ref: Optional[str] = None,
) -> GrantProposal:
    proposal = db.session.scalars(
        select(GrantProposal).where(
            GrantProposal.id == proposal_id,
            GrantProposal.organization_id == organization_id,
        ).limit(1)
    ).first()
    if proposal is None:
        raise LookupError("proposal not found for organization")

    if not proposal.narrative_summary:
        raise ValueError("cannot submit proposal without narrative_summary")
    if proposal.amount_requested is None or float(proposal.amount_requested) <= 0:
        raise ValueError("cannot submit proposal without amount_requested")

    effective_doc_ref = (document_ref or proposal.document_ref or "").strip()
    if not effective_doc_ref:
        raise ValueError("cannot submit proposal without document_ref")

    before = {
        "proposal_outcome": proposal.outcome,
        "opportunity_status": proposal.opportunity.status,
    }

    proposal.submission_date = submission_date
    proposal.document_ref = effective_doc_ref
    proposal.outcome = "submitted"
    proposal.opportunity.status = "submitted"
    db.session.commit()

    audit(
        "grant.proposal.submit",
        entity_type="grant_proposal",
        entity_id=int(proposal.id),
        details={
            "organization_id": int(organization_id),
            "before": before,
            "after": {
                "proposal_outcome": proposal.outcome,
                "opportunity_status": proposal.opportunity.status,
                "submission_date": submission_date.isoformat(),
            },
        },
    )
    return proposal


def set_proposal_outcome(
    proposal_id: int,
    organization_id: int,
    *,
    outcome: str,
) -> GrantProposal:
    if outcome not in _VALID_PROPOSAL_OUTCOMES:
        raise ValueError(f"invalid proposal outcome '{outcome}'")

    proposal = db.session.scalars(
        select(GrantProposal).where(
            GrantProposal.id == proposal_id,
            GrantProposal.organization_id == organization_id,
        ).limit(1)
    ).first()
    if proposal is None:
        raise LookupError("proposal not found for organization")

    if outcome in {"awarded", "declined", "withdrawn"} and proposal.outcome != "submitted":
        raise ValueError("proposal outcome can only move to awarded/declined/withdrawn from submitted")

    before = {
        "proposal_outcome": proposal.outcome,
        "opportunity_status": proposal.opportunity.status,
    }

    proposal.outcome = outcome
    if outcome == "awarded":
        proposal.opportunity.status = "awarded"
    elif outcome == "declined":
        proposal.opportunity.status = "declined"
    elif outcome == "withdrawn":
        proposal.opportunity.status = "qualified"

    db.session.commit()
    audit(
        "grant.proposal.outcome",
        entity_type="grant_proposal",
        entity_id=int(proposal.id),
        details={
            "organization_id": int(organization_id),
            "before": before,
            "after": {
                "proposal_outcome": proposal.outcome,
                "opportunity_status": proposal.opportunity.status,
            },
        },
    )
    return proposal


def convert_opportunity_to_grant(
    opportunity_id: int,
    organization_id: int,
    *,
    amount_awarded: float,
    award_date: Optional[date] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    create_grant_fn=None,
    advance_grant_status_fn=None,
    today_fn=None,
) -> Grant:
    opportunity = db.session.scalars(
        select(GrantOpportunity).where(
            GrantOpportunity.id == opportunity_id,
            GrantOpportunity.organization_id == organization_id,
        ).limit(1)
    ).first()
    if opportunity is None:
        raise LookupError("opportunity not found for organization")

    if opportunity.awarded_grant_id is not None:
        raise ValueError("opportunity already linked to awarded grant")
    if float(amount_awarded) <= 0:
        raise ValueError("amount_awarded must be positive")
    if opportunity.status != "awarded":
        raise ValueError("opportunity must be in awarded status before conversion")

    awarded_proposal_exists = db.session.scalar(
        select(func.count(GrantProposal.id)).where(
            GrantProposal.organization_id == organization_id,
            GrantProposal.opportunity_id == opportunity_id,
            GrantProposal.outcome == "awarded",
        )
    )
    if int(awarded_proposal_exists or 0) <= 0:
        raise ValueError("opportunity conversion requires at least one awarded proposal")

    if create_grant_fn is None or advance_grant_status_fn is None or today_fn is None:
        raise ValueError("conversion dependencies are required")

    grant = create_grant_fn(
        organization_id=organization_id,
        funder_name=opportunity.funder_name,
        title=opportunity.title,
        amount_requested=opportunity.amount_max or opportunity.amount_min,
        application_deadline=opportunity.deadline,
        start_date=start_date,
        end_date=end_date,
        notes=opportunity.notes,
    )
    advance_grant_status_fn(grant.id, organization_id, new_status="submitted")
    advance_grant_status_fn(
        grant.id,
        organization_id,
        new_status="awarded",
        amount_awarded=float(amount_awarded),
        award_date=award_date or today_fn(),
    )

    opportunity.awarded_grant_id = int(grant.id)
    db.session.commit()
    audit(
        "grant.opportunity.convert",
        entity_type="grant_opportunity",
        entity_id=int(opportunity.id),
        details={
            "organization_id": int(organization_id),
            "grant_id": int(grant.id),
            "amount_awarded": float(amount_awarded),
        },
    )
    return grant


def opportunity_forecast_summary(organization_id: int) -> dict:
    active_statuses = ["identified", "qualified", "in_progress", "submitted"]
    opportunities = list(
        db.session.scalars(
            select(GrantOpportunity)
            .where(GrantOpportunity.organization_id == organization_id, GrantOpportunity.status.in_(active_statuses))
            .order_by(GrantOpportunity.deadline.asc().nullslast())
        )
    )

    pipeline_count = len(opportunities)
    pipeline_amount = 0.0
    weighted_amount = 0.0
    for item in opportunities:
        if item.amount_min is None and item.amount_max is None:
            amount_basis = 0.0
        elif item.amount_min is None:
            amount_basis = float(item.amount_max or 0)
        elif item.amount_max is None:
            amount_basis = float(item.amount_min or 0)
        else:
            amount_basis = (float(item.amount_min) + float(item.amount_max)) / 2.0
        pipeline_amount += amount_basis
        weighted_amount += float(item.probability_weighted_amount or 0)

    return {
        "pipeline_count": pipeline_count,
        "pipeline_amount": round(pipeline_amount, 2),
        "probability_weighted_amount": round(weighted_amount, 2),
    }
