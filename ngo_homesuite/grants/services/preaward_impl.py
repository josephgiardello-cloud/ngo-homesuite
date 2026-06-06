from __future__ import annotations

from datetime import date
import json
from typing import Optional

from flask import current_app, has_app_context
from sqlalchemy import func, or_, select

from ngo_homesuite.db.utils import audit
from ngo_homesuite.models.core import Grant, GrantOpportunity, GrantProposal, db


_VALID_OPPORTUNITY_STATUSES = {"identified", "qualified", "in_progress", "submitted", "awarded", "declined", "archived"}
_VALID_PROPOSAL_OUTCOMES = {"draft", "submitted", "awarded", "declined", "withdrawn"}
_ACTIVE_OPPORTUNITY_STATUSES = {"identified", "qualified", "in_progress", "submitted"}


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


def _tokenize(text: str) -> set[str]:
    raw = "".join(ch.lower() if ch.isalnum() else " " for ch in str(text or ""))
    return {token for token in raw.split() if len(token) >= 3}


def _extract_requirement_items(source: str) -> list[str]:
    lines = [str(line or "").strip(" -\t\r") for line in str(source or "").splitlines()]
    candidates: list[str] = []
    for line in lines:
        if not line:
            continue
        lower = line.lower()
        if any(marker in lower for marker in ("must", "shall", "required", "requirement", "include", "compliance")):
            candidates.append(line)
    if not candidates and source:
        trimmed = str(source).strip()
        if trimmed:
            candidates = [trimmed]
    deduped: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped[:20]


def _build_requirement_sentence(requirement: str, *, program_summary: str, organization_summary: str) -> str:
    requirement_text = str(requirement or "").strip().rstrip(".")
    program_text = str(program_summary or "").strip()
    organization_text = str(organization_summary or "").strip()
    fragments = []
    if program_text:
        fragments.append(program_text)
    if organization_text:
        fragments.append(organization_text)
    context = " ".join(fragments).strip()
    if context:
        return f"{context}. This proposal addresses the requirement to {requirement_text.lower()}."
    return f"This proposal addresses the requirement to {requirement_text.lower()}."


def _opportunity_requirement_source(opportunity: GrantOpportunity) -> str:
    external_details = opportunity.external_details_json or {}
    detail_parts: list[str] = []
    if isinstance(external_details, dict):
        for key in (
            "summary",
            "description",
            "eligibility",
            "disqualifications",
            "application_guidance",
            "requirements",
            "applicable_conditions",
            "categories",
        ):
            value = external_details.get(key)
            if isinstance(value, list):
                detail_parts.append("\n".join(str(item or "") for item in value if str(item or "").strip()))
            elif value is not None:
                detail_parts.append(str(value))
    return "\n".join(
        [
            str(opportunity.title or ""),
            str(opportunity.program_name or ""),
            str(opportunity.notes or ""),
            str(opportunity.external_url or ""),
            "\n".join(part for part in detail_parts if part.strip()),
        ]
    ).strip()


def _append_opportunity_note(existing_notes: Optional[str], addition: str) -> str:
    current = str(existing_notes or "").strip()
    new_block = str(addition or "").strip()
    if not current:
        return new_block
    if not new_block:
        return current
    return f"{current}\n\n{new_block}"


def search_applicable_opportunities(
    organization_id: int,
    *,
    q: Optional[str] = None,
    applicant_profile: Optional[str] = None,
    requested_amount: Optional[float] = None,
    deadline_before: Optional[date] = None,
    statuses: Optional[list[str]] = None,
    limit: int = 50,
) -> list[dict]:
    status_values = statuses or sorted(_ACTIVE_OPPORTUNITY_STATUSES)
    for status in status_values:
        if status not in _VALID_OPPORTUNITY_STATUSES:
            raise ValueError(f"invalid opportunity status '{status}'")

    requested_amount_value: Optional[float] = None
    if requested_amount is not None:
        requested_amount_value = float(requested_amount)
        if requested_amount_value < 0:
            raise ValueError("requested_amount cannot be negative")

    stmt = select(GrantOpportunity).where(
        GrantOpportunity.organization_id == int(organization_id),
        GrantOpportunity.status.in_(status_values),
    )
    today = date.today()
    stmt = stmt.where(
        or_(GrantOpportunity.deadline.is_(None), GrantOpportunity.deadline >= today)
    )
    if deadline_before is not None:
        stmt = stmt.where(GrantOpportunity.deadline.is_not(None), GrantOpportunity.deadline <= deadline_before)

    opportunities = list(
        db.session.scalars(
            stmt.order_by(GrantOpportunity.deadline.asc().nullslast(), GrantOpportunity.probability.desc())
            .limit(max(1, min(int(limit), 200)))
        )
    )

    q_tokens = _tokenize(str(q or ""))
    profile_tokens = _tokenize(str(applicant_profile or ""))

    scored: list[dict] = []
    for opp in opportunities:
        searchable = " ".join(
            [
                str(opp.title or ""),
                str(opp.program_name or ""),
                str(opp.funder_name or ""),
                str(opp.notes or ""),
                str(opp.external_url or ""),
                json.dumps(opp.external_details_json or {}, sort_keys=True),
            ]
        )
        searchable_tokens = _tokenize(searchable)

        score = 0.0
        reasons: list[str] = []

        if q_tokens:
            overlap = q_tokens & searchable_tokens
            if not overlap:
                continue
            score += min(40.0, float(len(overlap) * 8))
            reasons.append(f"Keyword overlap: {', '.join(sorted(overlap)[:6])}")

        if profile_tokens:
            profile_overlap = profile_tokens & searchable_tokens
            if profile_overlap:
                score += min(25.0, float(len(profile_overlap) * 5))
                reasons.append(f"Applicant profile alignment: {', '.join(sorted(profile_overlap)[:6])}")

        probability = float(opp.probability or 0.0)
        score += probability * 20.0
        if probability > 0:
            reasons.append(f"Pipeline probability: {round(probability * 100, 1)}%")

        if requested_amount_value is not None:
            min_amt = float(opp.amount_min) if opp.amount_min is not None else None
            max_amt = float(opp.amount_max) if opp.amount_max is not None else None
            if min_amt is not None and requested_amount_value < min_amt:
                continue
            if max_amt is not None and requested_amount_value > max_amt:
                continue
            if min_amt is not None or max_amt is not None:
                score += 20.0
                reasons.append("Requested amount is within grant range")

        scored.append(
            {
                "opportunity_id": int(opp.id),
                "title": str(opp.title or ""),
                "program_name": str(opp.program_name or ""),
                "funder_name": str(opp.funder_name or ""),
                "status": str(opp.status or ""),
                "deadline": opp.deadline.isoformat() if opp.deadline else None,
                "amount_min": float(opp.amount_min) if opp.amount_min is not None else None,
                "amount_max": float(opp.amount_max) if opp.amount_max is not None else None,
                "probability": float(opp.probability or 0.0),
                "probability_weighted_amount": float(opp.probability_weighted_amount or 0.0),
                "external_source": opp.external_source,
                "external_opportunity_id": opp.external_opportunity_id,
                "external_url": opp.external_url,
                "external_details": opp.external_details_json or {},
                "applicability_score": round(score, 2),
                "match_reasons": reasons,
                "selectable": True,
            }
        )

    scored.sort(key=lambda item: (float(item["applicability_score"]), float(item["probability"])), reverse=True)
    return scored


def generate_proposal_compliance_guidance(
    opportunity_id: int,
    organization_id: int,
    *,
    proposal_text: Optional[str] = None,
) -> dict:
    opportunity = db.session.scalars(
        select(GrantOpportunity).where(
            GrantOpportunity.id == int(opportunity_id),
            GrantOpportunity.organization_id == int(organization_id),
        ).limit(1)
    ).first()
    if opportunity is None:
        raise LookupError("opportunity not found for organization")

    requirement_source = _opportunity_requirement_source(opportunity)
    requirement_items = _extract_requirement_items(requirement_source)

    proposal_body = str(proposal_text or "").strip()
    proposal_tokens = _tokenize(proposal_body)

    covered: list[str] = []
    missing: list[str] = []
    for item in requirement_items:
        item_tokens = _tokenize(item)
        if item_tokens and (item_tokens & proposal_tokens):
            covered.append(item)
        else:
            missing.append(item)

    total_items = max(1, len(requirement_items))
    deterministic_score = round((len(covered) / total_items) * 100.0, 1)
    risk_level = "low" if deterministic_score >= 80 else "medium" if deterministic_score >= 50 else "high"

    external_details = opportunity.external_details_json or {}
    guidance = {
        "opportunity_id": int(opportunity.id),
        "title": str(opportunity.title or ""),
        "external_source": opportunity.external_source,
        "external_opportunity_id": opportunity.external_opportunity_id,
        "external_url": opportunity.external_url,
        "external_details": external_details,
        "eligibility": list(external_details.get("eligibility") or []),
        "disqualifications": list(external_details.get("disqualifications") or []),
        "application_guidance": list(external_details.get("application_guidance") or []),
        "applicable_conditions": list(external_details.get("applicable_conditions") or []),
        "compliance_terms": requirement_items,
        "covered_terms": covered,
        "missing_terms": missing,
        "compliance_score": deterministic_score,
        "risk_level": risk_level,
        "recommended_outline": [
            "Eligibility and mission-fit summary",
            "Problem statement with measurable need",
            "Program design and implementation plan",
            "Budget narrative aligned to allowable costs",
            "Evaluation and reporting methodology",
            "Timeline, milestones, and staffing",
            "Sustainability and risk mitigation",
        ],
        "generated_by": "deterministic",
    }

    ai_enabled = False
    if has_app_context():
        ai_enabled = bool(current_app.config.get("GRANT_COMPLIANCE_AI_ASSIST_ENABLED", False))

    if ai_enabled:
        try:
            from ngo_homesuite.ai.apex_client import ApexClient

            host = current_app.config.get("OLLAMA_HOST", "http://localhost:11434")
            model = current_app.config.get("OLLAMA_MODEL", "llama3.2")
            timeout_s = float(current_app.config.get("OLLAMA_TIMEOUT_S", 45.0))

            prompt = (
                "You are a grant compliance reviewer. Return strict JSON with keys: "
                "compliance_score, risk_level, missing_terms, covered_terms, recommended_edits, approval_readiness_summary. "
                f"Grant opportunity context:\n{requirement_source}\n\n"
                f"Proposal draft:\n{proposal_body or '[empty]'}"
            )
            client = ApexClient(host=str(host), model=str(model), timeout_s=timeout_s)
            raw = client.query(
                prompt=prompt,
                model=str(model),
                system_prompt="You are a strict nonprofit grant-compliance minion. Return JSON only.",
            )
            parsed = json.loads(raw)
            guidance.update(
                {
                    "compliance_score": float(parsed.get("compliance_score", guidance["compliance_score"])),
                    "risk_level": str(parsed.get("risk_level", guidance["risk_level"])),
                    "missing_terms": list(parsed.get("missing_terms") or guidance["missing_terms"]),
                    "covered_terms": list(parsed.get("covered_terms") or guidance["covered_terms"]),
                    "recommended_edits": list(parsed.get("recommended_edits") or []),
                    "approval_readiness_summary": str(parsed.get("approval_readiness_summary") or ""),
                    "generated_by": "ai",
                }
            )
        except Exception:
            pass

    return guidance


def generate_proposal_draft_assist(
    opportunity_id: int,
    organization_id: int,
    *,
    organization_summary: Optional[str] = None,
    program_summary: Optional[str] = None,
    applicant_profile: Optional[str] = None,
    amount_requested: Optional[float] = None,
    existing_draft: Optional[str] = None,
) -> dict:
    opportunity = db.session.scalars(
        select(GrantOpportunity).where(
            GrantOpportunity.id == int(opportunity_id),
            GrantOpportunity.organization_id == int(organization_id),
        ).limit(1)
    ).first()
    if opportunity is None:
        raise LookupError("opportunity not found for organization")

    amount_requested_value = None
    if amount_requested is not None:
        amount_requested_value = float(amount_requested)
        if amount_requested_value < 0:
            raise ValueError("amount_requested cannot be negative")

    organization_text = str(organization_summary or "").strip()
    program_text = str(program_summary or "").strip()
    applicant_text = str(applicant_profile or "").strip()
    existing_text = str(existing_draft or "").strip()

    guidance = generate_proposal_compliance_guidance(
        int(opportunity.id),
        int(organization_id),
        proposal_text=existing_text or None,
    )
    compliance_terms = list(guidance.get("compliance_terms") or [])

    section_text = {
        "eligibility_and_mission_fit": (
            f"{organization_text or 'Our organization'} is well positioned to deliver {opportunity.program_name} because "
            f"{applicant_text or 'our mission, service history, and target population align closely with the funder priorities'}."
        ),
        "problem_statement": (
            f"{program_text or 'The proposed program'} responds to a documented community need with measurable goals, a defined target population, "
            f"and a delivery model matched to {opportunity.title}."
        ),
        "program_design": (
            f"We will implement {program_text or opportunity.program_name} through clear milestones, staff ownership, service delivery checkpoints, "
            "and a monitoring cadence that keeps performance and compliance visible throughout the grant period."
        ),
        "budget_and_compliance": (
            f"The requested budget of ${amount_requested_value:,.2f} will be restricted to allowable grant activities and documented with complete backup, "
            "internal controls, and periodic compliance review."
            if amount_requested_value is not None
            else "The budget narrative will tie each line item to allowable grant activities, required controls, and documentation standards."
        ),
        "evaluation_and_reporting": (
            "We will track outputs, outcomes, and reporting deadlines through a formal review calendar, evidence collection process, and corrective-action workflow."
        ),
    }

    requirement_to_draft = []
    for index, requirement in enumerate(compliance_terms, start=1):
        if index == 1:
            section_key = "eligibility_and_mission_fit"
        elif index == 2:
            section_key = "program_design"
        elif index == 3:
            section_key = "budget_and_compliance"
        else:
            section_key = "evaluation_and_reporting"
        drafted_sentence = _build_requirement_sentence(
            requirement,
            program_summary=program_text or opportunity.program_name,
            organization_summary=organization_text or applicant_text,
        )
        section_text[section_key] = f"{section_text[section_key]} {drafted_sentence}".strip()
        requirement_to_draft.append(
            {
                "requirement": requirement,
                "section": section_key,
                "draft_text": drafted_sentence,
                "status": "covered" if requirement in set(guidance.get("covered_terms") or []) else "needs_strengthening",
            }
        )

    missing_terms = list(guidance.get("missing_terms") or [])
    revision_suggestions = [
        f"Add explicit language showing how the proposal will satisfy: {term}"
        for term in missing_terms
    ]
    if amount_requested_value is None:
        revision_suggestions.append("Add the requested funding amount so the budget narrative can be tailored to the funder range.")
    if not organization_text:
        revision_suggestions.append("Add a concise organization capability summary to strengthen the eligibility and mission-fit section.")
    if not program_text:
        revision_suggestions.append("Add a program summary with measurable activities and outcomes for a stronger project design section.")

    draft_assist = {
        "opportunity_id": int(opportunity.id),
        "title": str(opportunity.title or ""),
        "external_source": opportunity.external_source,
        "external_opportunity_id": opportunity.external_opportunity_id,
        "external_url": opportunity.external_url,
        "external_details": opportunity.external_details_json or {},
        "eligibility": list(guidance.get("eligibility") or []),
        "disqualifications": list(guidance.get("disqualifications") or []),
        "application_guidance": list(guidance.get("application_guidance") or []),
        "applicable_conditions": list(guidance.get("applicable_conditions") or []),
        "generated_by": "deterministic",
        "draft_sections": section_text,
        "requirement_to_draft": requirement_to_draft,
        "revision_suggestions": revision_suggestions,
        "approval_readiness_summary": (
            f"Draft generated with {len(requirement_to_draft)} mapped compliance terms. "
            f"Current compliance score is {guidance.get('compliance_score', 0)} with {len(missing_terms)} terms still needing explicit coverage."
        ),
        "compliance_score": guidance.get("compliance_score", 0),
        "risk_level": guidance.get("risk_level", "medium"),
    }

    ai_enabled = False
    if has_app_context():
        ai_enabled = bool(current_app.config.get("GRANT_COMPLIANCE_AI_ASSIST_ENABLED", False))

    if ai_enabled:
        try:
            from ngo_homesuite.ai.apex_client import ApexClient

            host = current_app.config.get("OLLAMA_HOST", "http://localhost:11434")
            model = current_app.config.get("OLLAMA_MODEL", "llama3.2")
            timeout_s = float(current_app.config.get("OLLAMA_TIMEOUT_S", 45.0))
            prompt = (
                "Return strict JSON with keys draft_sections, requirement_to_draft, revision_suggestions, approval_readiness_summary. "
                f"Grant context:\n{_opportunity_requirement_source(opportunity)}\n\n"
                f"Organization summary: {organization_text or '[missing]'}\n"
                f"Program summary: {program_text or '[missing]'}\n"
                f"Applicant profile: {applicant_text or '[missing]'}\n"
                f"Requested amount: {amount_requested_value if amount_requested_value is not None else '[missing]'}\n"
                f"Existing draft: {existing_text or '[empty]'}\n"
                f"Compliance terms: {json.dumps(compliance_terms)}"
            )
            client = ApexClient(host=str(host), model=str(model), timeout_s=timeout_s)
            raw = client.query(
                prompt=prompt,
                model=str(model),
                system_prompt="You are a strict nonprofit grant-writing minion. Return JSON only.",
            )
            parsed = json.loads(raw)
            draft_assist.update(
                {
                    "draft_sections": dict(parsed.get("draft_sections") or draft_assist["draft_sections"]),
                    "requirement_to_draft": list(parsed.get("requirement_to_draft") or draft_assist["requirement_to_draft"]),
                    "revision_suggestions": list(parsed.get("revision_suggestions") or draft_assist["revision_suggestions"]),
                    "approval_readiness_summary": str(
                        parsed.get("approval_readiness_summary") or draft_assist["approval_readiness_summary"]
                    ),
                    "generated_by": "ai",
                }
            )
        except Exception:
            pass

    audit(
        "grant.proposal.draft_assist",
        entity_type="grant_opportunity",
        entity_id=int(opportunity.id),
        details={
            "organization_id": int(organization_id),
            "generated_by": draft_assist["generated_by"],
            "compliance_score": draft_assist["compliance_score"],
            "risk_level": draft_assist["risk_level"],
        },
    )

    return draft_assist


def get_opportunity_ai_context(opportunity_id: int, organization_id: int) -> dict:
    opportunity = db.session.scalars(
        select(GrantOpportunity).where(
            GrantOpportunity.id == int(opportunity_id),
            GrantOpportunity.organization_id == int(organization_id),
        ).limit(1)
    ).first()
    if opportunity is None:
        raise LookupError("opportunity not found for organization")

    external_details = opportunity.external_details_json or {}
    compliance = generate_proposal_compliance_guidance(
        int(opportunity.id),
        int(organization_id),
        proposal_text=None,
    )
    recent_proposals = list(
        db.session.scalars(
            select(GrantProposal).where(
                GrantProposal.opportunity_id == int(opportunity.id),
                GrantProposal.organization_id == int(organization_id),
            ).order_by(GrantProposal.version_number.desc()).limit(5)
        )
    )
    return {
        "opportunity_id": int(opportunity.id),
        "title": str(opportunity.title or ""),
        "funder_name": str(opportunity.funder_name or ""),
        "program_name": str(opportunity.program_name or ""),
        "deadline": opportunity.deadline.isoformat() if opportunity.deadline else None,
        "external_source": opportunity.external_source,
        "external_opportunity_id": opportunity.external_opportunity_id,
        "external_url": opportunity.external_url,
        "notes": str(opportunity.notes or ""),
        "summary": str(external_details.get("summary") or ""),
        "eligibility": list(external_details.get("eligibility") or []),
        "disqualifications": list(external_details.get("disqualifications") or []),
        "application_guidance": list(external_details.get("application_guidance") or []),
        "applicable_conditions": list(external_details.get("applicable_conditions") or []),
        "requirements": list(external_details.get("requirements") or compliance.get("compliance_terms") or []),
        "categories": list(external_details.get("categories") or []),
        "agency_contact_name": str(external_details.get("agency_contact_name") or ""),
        "agency_contact_email": str(external_details.get("agency_contact_email") or ""),
        "compliance_terms": list(compliance.get("compliance_terms") or []),
        "recommended_outline": list(compliance.get("recommended_outline") or []),
        "recent_proposals": [
            {
                "id": int(proposal.id),
                "version": int(proposal.version),
                "status": proposal.status,
                "amount_requested": float(proposal.amount_requested or 0.0),
                "document_ref": proposal.document_ref,
                "narrative_summary": str(proposal.narrative_summary or ""),
            }
            for proposal in recent_proposals
        ],
        "external_details": external_details,
    }


def ingest_opportunity_guidance(
    opportunity_id: int,
    organization_id: int,
    *,
    guideline_text: str,
    source_name: Optional[str] = None,
    merge_into_notes: bool = True,
) -> dict:
    opportunity = db.session.scalars(
        select(GrantOpportunity).where(
            GrantOpportunity.id == int(opportunity_id),
            GrantOpportunity.organization_id == int(organization_id),
        ).limit(1)
    ).first()
    if opportunity is None:
        raise LookupError("opportunity not found for organization")

    raw_text = str(guideline_text or "").strip()
    if not raw_text:
        raise ValueError("guideline_text is required")

    requirement_items = _extract_requirement_items(raw_text)
    normalized_block_lines = [
        f"Guideline source: {str(source_name or 'manual').strip() or 'manual'}",
        "Extracted compliance requirements:",
    ]
    normalized_block_lines.extend(f"- {item}" for item in requirement_items)
    normalized_block = "\n".join(normalized_block_lines)

    if merge_into_notes:
        opportunity.notes = _append_opportunity_note(opportunity.notes, normalized_block)
        db.session.commit()
    else:
        db.session.flush()

    audit(
        "grant.opportunity.guidance_ingest",
        entity_type="grant_opportunity",
        entity_id=int(opportunity.id),
        details={
            "organization_id": int(organization_id),
            "source_name": str(source_name or "manual"),
            "merge_into_notes": bool(merge_into_notes),
            "requirement_count": len(requirement_items),
        },
    )

    return {
        "opportunity_id": int(opportunity.id),
        "source_name": str(source_name or "manual"),
        "merge_into_notes": bool(merge_into_notes),
        "requirement_count": len(requirement_items),
        "requirements": requirement_items,
        "notes_updated": bool(merge_into_notes),
        "notes_preview": str(opportunity.notes or "")[-1200:] if merge_into_notes else None,
    }


def save_draft_assist_as_proposal(
    opportunity_id: int,
    organization_id: int,
    *,
    organization_summary: Optional[str] = None,
    program_summary: Optional[str] = None,
    applicant_profile: Optional[str] = None,
    amount_requested: Optional[float] = None,
    existing_draft: Optional[str] = None,
    document_ref: Optional[str] = None,
) -> GrantProposal:
    draft = generate_proposal_draft_assist(
        opportunity_id,
        organization_id,
        organization_summary=organization_summary,
        program_summary=program_summary,
        applicant_profile=applicant_profile,
        amount_requested=amount_requested,
        existing_draft=existing_draft,
    )
    sections = dict(draft.get("draft_sections") or {})
    section_order = [
        "eligibility_and_mission_fit",
        "problem_statement",
        "program_design",
        "budget_and_compliance",
        "evaluation_and_reporting",
    ]
    narrative_parts = []
    for section_name in section_order:
        value = str(sections.get(section_name) or "").strip()
        if not value:
            continue
        heading = section_name.replace("_", " ").title()
        narrative_parts.append(f"{heading}\n{value}")
    narrative_summary = "\n\n".join(narrative_parts).strip()
    if not narrative_summary:
        raise ValueError("draft assist did not produce proposal content")

    generated_document_ref = str(document_ref or "").strip() or f"draft-assist-opportunity-{int(opportunity_id)}.md"
    trace_payload = {
        "requirement_to_draft": draft.get("requirement_to_draft") or [],
        "revision_suggestions": draft.get("revision_suggestions") or [],
        "approval_readiness_summary": draft.get("approval_readiness_summary") or "",
        "generated_by": draft.get("generated_by") or "deterministic",
        "compliance_score": draft.get("compliance_score") or 0,
        "risk_level": draft.get("risk_level") or "medium",
    }
    proposal_notes = f"Draft assist trace\n{json.dumps(trace_payload, sort_keys=True)}"

    proposal = create_proposal(
        int(opportunity_id),
        int(organization_id),
        amount_requested=amount_requested,
        narrative_summary=narrative_summary,
        document_ref=generated_document_ref,
        notes=proposal_notes,
    )
    audit(
        "grant.proposal.draft_assist_saved",
        entity_type="grant_proposal",
        entity_id=int(proposal.id),
        details={
            "organization_id": int(organization_id),
            "opportunity_id": int(opportunity_id),
            "generated_by": trace_payload["generated_by"],
            "compliance_score": trace_payload["compliance_score"],
        },
    )
    return proposal


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
    external_source: Optional[str] = None,
    external_opportunity_id: Optional[str] = None,
    external_url: Optional[str] = None,
    external_details_json: Optional[dict] = None,
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
        external_source=(external_source or "").strip() or None,
        external_opportunity_id=(external_opportunity_id or "").strip() or None,
        external_url=(external_url or "").strip() or None,
        external_details_json=external_details_json or None,
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

    for key in ["external_source", "external_opportunity_id", "external_url"]:
        if key in fields:
            setattr(opportunity, key, str(fields[key] or "").strip() or None)

    if "external_details_json" in fields:
        opportunity.external_details_json = fields["external_details_json"] or None

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

