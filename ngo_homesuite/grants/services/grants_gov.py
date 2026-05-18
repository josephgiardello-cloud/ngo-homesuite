from __future__ import annotations

from datetime import date, datetime, timezone
import json
import logging
from typing import Any, Optional
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from flask import current_app, has_app_context
from sqlalchemy import select

from ngo_homesuite.db.utils import audit
from ngo_homesuite.grants.models import GrantOpportunity, GrantSearchAlert, GrantSearchProfile
from ngo_homesuite.models.core import db

logger = logging.getLogger(__name__)

_DEFAULT_XML_EXTRACT_URL = "https://www.grants.gov/xml-extract"

# ---------------------------------------------------------------------------
# Grants.gov XML feed field constants
# These are the authoritative field names present in the Grants.gov XML extract
# (GrantsDBExtract*.xml).  Ordering within each tuple is: preferred first,
# then known legacy / alternate names that appear in older extracts.
# ---------------------------------------------------------------------------
_FIELD_OPPORTUNITY_ID = (
    "OpportunityID",
    "FundingOpportunityNumber",
    "OpportunityNumber",
    "id",
)
_FIELD_TITLE = (
    "OpportunityTitle",
    "FundingOpportunityTitle",
    "title",
)
_FIELD_AGENCY_NAME = (
    "AgencyName",
    "AgencyCode",
    "Agency",
    "AgencyContactName",
)
_FIELD_CLOSE_DATE = (
    "CloseDate",
    "ApplicationDueDate",
    "closeDate",
    "ClosingDate",
)
_FIELD_AWARD_FLOOR = (
    "AwardFloor",
    "MinimumAward",
    "EstimatedFundingFloor",
)
_FIELD_AWARD_CEILING = (
    "AwardCeiling",
    "MaximumAward",
    "EstimatedFunding",
)
_FIELD_SYNOPSIS = (
    "Synopsis",
    "Description",
    "FundingDescription",
    "OpportunityCategoryExplanation",
    "AdditionalInformation",
)
_FIELD_ELIGIBILITY = (
    "EligibleApplicants",
    "EligibilityDesc",
    "Eligibility",
    "AdditionalInformationOnEligibility",
)
_FIELD_DISQUALIFICATION = (
    "IneligibleApplicants",
    "Disqualification",
    "Ineligibility",
    "OtherEligibility",
)
_FIELD_APPLICATION_GUIDANCE = (
    "ApplicationInstructions",
    "HowToApply",
    "GrantWritingGuidance",
    "ApplicationWritingGuidance",
)
_FIELD_CONDITIONS = (
    "ApplicableConditions",
    "AwardCeilingDescription",
    "AwardFloorDescription",
    "Restrictions",
)
_FIELD_REQUIREMENTS = (
    "PostAwardReportingRequirements",
    "Requirements",
    "CloseDateExplanation",
)
_FIELD_CATEGORY = (
    "CategoryOfFundingActivity",
    "FundingInstrumentType",
    "CategoryExplanation",
)
_FIELD_PROGRAM_NAME = (
    "CFDANumbers",
    "CategoryOfFundingActivity",
    "FundingCategory",
    "programName",
)
_FIELD_URL = (
    "OpportunityURL",
    "GrantURL",
    "ApplicationURL",
    "URL",
)
_FIELD_CONTACT_EMAIL = (
    "AgencyContactEmail",
    "ContactEmail",
    "Email",
)
_FIELD_CONTACT_NAME = (
    "AgencyContactName",
    "ContactName",
)


def _normalize_tag(tag: str) -> str:
    return str(tag or "").split("}")[-1].strip().lower()


def _tokenize(text: str) -> set[str]:
    raw = "".join(ch.lower() if ch.isalnum() else " " for ch in str(text or ""))
    return {token for token in raw.split() if len(token) >= 3}


def _coerce_date(value: Any) -> date | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    for parser in (
        lambda v: date.fromisoformat(v[:10]),
        lambda v: datetime.strptime(v[:10], "%m/%d/%Y").date(),
        lambda v: datetime.strptime(v[:10], "%Y/%m/%d").date(),
    ):
        try:
            return parser(raw)
        except ValueError:
            continue
    return None


def _coerce_float(value: Any) -> float | None:
    raw = str(value or "").strip().replace(",", "")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _split_detail_items(value: Any) -> list[str]:
    raw = str(value or "").replace("\r", "\n")
    if not raw.strip():
        return []
    pieces = []
    for chunk in raw.replace(";", "\n").splitlines():
        item = chunk.strip(" -*\t")
        if item:
            pieces.append(item)
    deduped: list[str] = []
    seen: set[str] = set()
    for item in pieces:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _find_text(element: ET.Element, *names: str) -> str:
    wanted = {_normalize_tag(name) for name in names}
    for node in element.iter():
        if _normalize_tag(node.tag) in wanted:
            text = " ".join(part.strip() for part in node.itertext() if str(part).strip()).strip()
            if text:
                return text
    return ""


def _extract_record_nodes(root: ET.Element) -> list[ET.Element]:
    nodes: list[ET.Element] = []
    for node in list(root):
        title = _find_text(node, *_FIELD_TITLE)
        external_id = _find_text(node, *_FIELD_OPPORTUNITY_ID)
        if title or external_id:
            nodes.append(node)
    if nodes:
        return nodes
    return [node for node in root.iter() if _find_text(node, *_FIELD_TITLE)]


def _normalize_grants_gov_record(node: ET.Element) -> dict[str, Any]:
    external_id = _find_text(node, *_FIELD_OPPORTUNITY_ID)
    title = _find_text(node, *_FIELD_TITLE) or external_id or "Untitled Grants.gov Opportunity"
    funder_name = _find_text(node, *_FIELD_AGENCY_NAME) or "Grants.gov"
    program_name = _find_text(node, *_FIELD_PROGRAM_NAME) or funder_name
    summary = _find_text(node, *_FIELD_SYNOPSIS)
    eligibility = _split_detail_items(_find_text(node, *_FIELD_ELIGIBILITY))
    disqualifications = _split_detail_items(_find_text(node, *_FIELD_DISQUALIFICATION))
    application_guidance = _split_detail_items(_find_text(node, *_FIELD_APPLICATION_GUIDANCE))
    applicable_conditions = _split_detail_items(_find_text(node, *_FIELD_CONDITIONS))
    requirements = _split_detail_items(_find_text(node, *_FIELD_REQUIREMENTS))
    categories = _split_detail_items(_find_text(node, *_FIELD_CATEGORY))
    opportunity_url = _find_text(node, *_FIELD_URL)
    if not opportunity_url and external_id:
        opportunity_url = f"https://www.grants.gov/search-results-detail/{external_id}"

    return {
        "external_source": "grants_gov",
        "external_opportunity_id": external_id or title,
        "title": title,
        "funder_name": funder_name,
        "program_name": program_name,
        "deadline": _coerce_date(_find_text(node, *_FIELD_CLOSE_DATE)),
        "amount_min": _coerce_float(_find_text(node, *_FIELD_AWARD_FLOOR)),
        "amount_max": _coerce_float(_find_text(node, *_FIELD_AWARD_CEILING)),
        "summary": summary,
        "eligibility": eligibility,
        "disqualifications": disqualifications,
        "application_guidance": application_guidance,
        "applicable_conditions": applicable_conditions,
        "requirements": requirements,
        "categories": categories,
        "external_url": opportunity_url,
        "agency_contact_email": _find_text(node, *_FIELD_CONTACT_EMAIL),
        "agency_contact_name": _find_text(node, *_FIELD_CONTACT_NAME),
        "source_payload": ET.tostring(node, encoding="unicode", method="xml"),
    }


def _fetch_xml_text(url: str) -> str:
    request = Request(url, headers={"User-Agent": "NGOHomeSuite/1.0 Grants Connector"})
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="ignore")


def _effective_xml_extract_url() -> str:
    if has_app_context():
        return str(current_app.config.get("GRANTS_GOV_XML_EXTRACT_URL") or _DEFAULT_XML_EXTRACT_URL)
    return _DEFAULT_XML_EXTRACT_URL


def fetch_grants_gov_opportunities(*, xml_text: Optional[str] = None) -> list[dict[str, Any]]:
    payload = str(xml_text or "").strip() or _fetch_xml_text(_effective_xml_extract_url())
    root = ET.fromstring(payload)
    nodes = _extract_record_nodes(root)
    return [_normalize_grants_gov_record(node) for node in nodes]


def search_grants_gov_opportunities(
    organization_id: int,
    *,
    q: Optional[str] = None,
    applicant_profile: Optional[str] = None,
    requested_amount: Optional[float] = None,
    limit: int = 25,
    xml_text: Optional[str] = None,
) -> list[dict[str, Any]]:
    requested_amount_value = float(requested_amount) if requested_amount is not None else None
    if requested_amount_value is not None and requested_amount_value < 0:
        raise ValueError("requested_amount cannot be negative")

    q_tokens = _tokenize(str(q or ""))
    applicant_tokens = _tokenize(str(applicant_profile or ""))
    opportunities = fetch_grants_gov_opportunities(xml_text=xml_text)
    scored: list[dict[str, Any]] = []
    for record in opportunities:
        search_space = " ".join(
            [
                str(record.get("title") or ""),
                str(record.get("funder_name") or ""),
                str(record.get("program_name") or ""),
                str(record.get("summary") or ""),
                " ".join(record.get("eligibility") or []),
                " ".join(record.get("disqualifications") or []),
                " ".join(record.get("application_guidance") or []),
                " ".join(record.get("applicable_conditions") or []),
                " ".join(record.get("requirements") or []),
                " ".join(record.get("categories") or []),
            ]
        )
        search_tokens = _tokenize(search_space)
        score = 0.0
        reasons: list[str] = []
        if q_tokens:
            overlap = q_tokens & search_tokens
            if not overlap:
                continue
            score += min(45.0, float(len(overlap) * 9))
            reasons.append(f"Keyword overlap: {', '.join(sorted(overlap)[:6])}")
        if applicant_tokens:
            overlap = applicant_tokens & search_tokens
            if overlap:
                score += min(25.0, float(len(overlap) * 5))
                reasons.append(f"Applicant fit: {', '.join(sorted(overlap)[:6])}")
        amount_min = record.get("amount_min")
        amount_max = record.get("amount_max")
        if requested_amount_value is not None:
            if amount_min is not None and requested_amount_value < float(amount_min):
                continue
            if amount_max is not None and requested_amount_value > float(amount_max):
                continue
            if amount_min is not None or amount_max is not None:
                score += 20.0
                reasons.append("Requested amount fits published range")
        if record.get("eligibility"):
            score += 5.0
            reasons.append("Eligibility details available")
        if record.get("application_guidance"):
            score += 5.0
            reasons.append("Application guidance available")
        normalized = dict(record)
        normalized["applicability_score"] = round(score, 2)
        normalized["match_reasons"] = reasons
        normalized["organization_id"] = int(organization_id)
        scored.append(normalized)
    scored.sort(key=lambda item: float(item.get("applicability_score", 0)), reverse=True)
    return scored[: max(1, min(int(limit), 100))]


def _build_notes_for_sync(record: dict[str, Any]) -> str:
    sections = []
    if record.get("summary"):
        sections.append(f"Summary: {record['summary']}")
    if record.get("eligibility"):
        sections.append("Eligibility:\n- " + "\n- ".join(record["eligibility"]))
    if record.get("disqualifications"):
        sections.append("Disqualifications:\n- " + "\n- ".join(record["disqualifications"]))
    if record.get("application_guidance"):
        sections.append("Application guidance:\n- " + "\n- ".join(record["application_guidance"]))
    if record.get("applicable_conditions"):
        sections.append("Applicable conditions:\n- " + "\n- ".join(record["applicable_conditions"]))
    if record.get("requirements"):
        sections.append("Requirements:\n- " + "\n- ".join(record["requirements"]))
    return "\n\n".join(sections)


def sync_grants_gov_results(organization_id: int, results: list[dict[str, Any]]) -> list[GrantOpportunity]:
    synced: list[GrantOpportunity] = []
    for record in results:
        external_id = str(record.get("external_opportunity_id") or "").strip()
        if not external_id:
            continue
        opportunity = db.session.scalars(
            select(GrantOpportunity).where(
                GrantOpportunity.organization_id == int(organization_id),
                GrantOpportunity.external_source == "grants_gov",
                GrantOpportunity.external_opportunity_id == external_id,
            ).limit(1)
        ).first()
        if opportunity is None:
            opportunity = GrantOpportunity(
                organization_id=int(organization_id),
                funder_name=str(record.get("funder_name") or "Grants.gov"),
                program_name=str(record.get("program_name") or record.get("funder_name") or "Federal Grant Opportunity"),
                title=str(record.get("title") or external_id),
                deadline=record.get("deadline"),
                amount_min=record.get("amount_min"),
                amount_max=record.get("amount_max"),
                probability=0.0,
                probability_weighted_amount=0.0,
                external_source="grants_gov",
                external_opportunity_id=external_id,
                external_url=str(record.get("external_url") or "").strip() or None,
                external_details_json={
                    "summary": record.get("summary") or "",
                    "eligibility": record.get("eligibility") or [],
                    "disqualifications": record.get("disqualifications") or [],
                    "application_guidance": record.get("application_guidance") or [],
                    "applicable_conditions": record.get("applicable_conditions") or [],
                    "requirements": record.get("requirements") or [],
                    "categories": record.get("categories") or [],
                    "agency_contact_email": record.get("agency_contact_email") or "",
                    "agency_contact_name": record.get("agency_contact_name") or "",
                    "source_payload": record.get("source_payload") or "",
                },
                status="identified",
                notes=_build_notes_for_sync(record),
            )
            db.session.add(opportunity)
        else:
            opportunity.funder_name = str(record.get("funder_name") or opportunity.funder_name)
            opportunity.program_name = str(record.get("program_name") or opportunity.program_name)
            opportunity.title = str(record.get("title") or opportunity.title)
            opportunity.deadline = record.get("deadline") or opportunity.deadline
            opportunity.amount_min = record.get("amount_min") if record.get("amount_min") is not None else opportunity.amount_min
            opportunity.amount_max = record.get("amount_max") if record.get("amount_max") is not None else opportunity.amount_max
            opportunity.external_url = str(record.get("external_url") or opportunity.external_url or "").strip() or None
            opportunity.external_details_json = {
                "summary": record.get("summary") or "",
                "eligibility": record.get("eligibility") or [],
                "disqualifications": record.get("disqualifications") or [],
                "application_guidance": record.get("application_guidance") or [],
                "applicable_conditions": record.get("applicable_conditions") or [],
                "requirements": record.get("requirements") or [],
                "categories": record.get("categories") or [],
                "agency_contact_email": record.get("agency_contact_email") or "",
                "agency_contact_name": record.get("agency_contact_name") or "",
                "source_payload": record.get("source_payload") or "",
            }
            opportunity.notes = _build_notes_for_sync(record)
        synced.append(opportunity)
    db.session.commit()
    if synced:
        audit(
            "grant.external.sync",
            entity_type="grant_opportunity",
            details={
                "organization_id": int(organization_id),
                "source": "grants_gov",
                "synced_count": len(synced),
            },
        )
    return synced


def create_search_profile(
    organization_id: int,
    *,
    name: str,
    source: str = "grants_gov",
    query: Optional[str] = None,
    applicant_profile: Optional[str] = None,
    requested_amount: Optional[float] = None,
    statuses_csv: Optional[str] = None,
    alert_channel: str = "in_app",
) -> GrantSearchProfile:
    clean_name = str(name or "").strip()
    if not clean_name:
        raise ValueError("name is required")
    profile = GrantSearchProfile(
        organization_id=int(organization_id),
        source=str(source or "grants_gov").strip() or "grants_gov",
        name=clean_name,
        query=str(query or "").strip() or None,
        applicant_profile=str(applicant_profile or "").strip() or None,
        requested_amount=float(requested_amount) if requested_amount is not None else None,
        statuses_csv=str(statuses_csv or "").strip() or None,
        alert_channel=str(alert_channel or "in_app").strip() or "in_app",
    )
    db.session.add(profile)
    db.session.commit()
    return profile


def list_search_profiles(organization_id: int, *, active_only: bool = False) -> list[GrantSearchProfile]:
    stmt = select(GrantSearchProfile).where(GrantSearchProfile.organization_id == int(organization_id))
    if active_only:
        stmt = stmt.where(GrantSearchProfile.is_active == True)
    stmt = stmt.order_by(GrantSearchProfile.created_at.desc())
    return list(db.session.scalars(stmt))


def run_search_profile(profile_id: int, organization_id: int, *, xml_text: Optional[str] = None) -> dict[str, Any]:
    profile = db.session.scalars(
        select(GrantSearchProfile).where(
            GrantSearchProfile.id == int(profile_id),
            GrantSearchProfile.organization_id == int(organization_id),
        ).limit(1)
    ).first()
    if profile is None:
        raise LookupError("search profile not found for organization")
    if profile.source != "grants_gov":
        raise ValueError("unsupported search profile source")

    results = search_grants_gov_opportunities(
        int(organization_id),
        q=profile.query,
        applicant_profile=profile.applicant_profile,
        requested_amount=profile.requested_amount,
        limit=25,
        xml_text=xml_text,
    )
    synced = sync_grants_gov_results(int(organization_id), results)
    synced_by_external = {str(item.external_opportunity_id or ""): item for item in synced}

    created_alerts = 0
    for result in results:
        external_id = str(result.get("external_opportunity_id") or "").strip()
        if not external_id:
            continue
        existing = db.session.scalars(
            select(GrantSearchAlert).where(
                GrantSearchAlert.profile_id == int(profile.id),
                GrantSearchAlert.external_source == "grants_gov",
                GrantSearchAlert.external_opportunity_id == external_id,
            ).limit(1)
        ).first()
        if existing is not None:
            continue
        linked_opportunity = synced_by_external.get(external_id)
        alert = GrantSearchAlert(
            organization_id=int(organization_id),
            profile_id=int(profile.id),
            opportunity_id=int(linked_opportunity.id) if linked_opportunity is not None else None,
            external_source="grants_gov",
            external_opportunity_id=external_id,
            title=str(result.get("title") or external_id),
            status="new",
            details_json={
                "funder_name": result.get("funder_name") or "",
                "program_name": result.get("program_name") or "",
                "external_url": result.get("external_url") or "",
                "eligibility": result.get("eligibility") or [],
                "disqualifications": result.get("disqualifications") or [],
                "application_guidance": result.get("application_guidance") or [],
                "applicable_conditions": result.get("applicable_conditions") or [],
                "requirements": result.get("requirements") or [],
                "applicability_score": result.get("applicability_score") or 0,
            },
        )
        db.session.add(alert)
        created_alerts += 1

    profile.last_checked_at = datetime.now(timezone.utc).replace(tzinfo=None)
    profile.last_result_count = len(results)
    db.session.commit()
    audit(
        "grant.search_profile.run",
        entity_type="grant_search_profile",
        entity_id=int(profile.id),
        details={
            "organization_id": int(organization_id),
            "result_count": len(results),
            "created_alerts": created_alerts,
        },
    )
    return {
        "profile_id": int(profile.id),
        "result_count": len(results),
        "created_alerts": created_alerts,
        "results": results,
    }


def list_search_alerts(organization_id: int, *, status: Optional[str] = None, limit: int = 50) -> list[dict[str, Any]]:
    stmt = select(GrantSearchAlert).where(GrantSearchAlert.organization_id == int(organization_id))
    if status:
        stmt = stmt.where(GrantSearchAlert.status == str(status))
    stmt = stmt.order_by(GrantSearchAlert.matched_at.desc()).limit(max(1, min(int(limit), 200)))
    alerts = list(db.session.scalars(stmt))
    return [
        {
            "id": int(alert.id),
            "profile_id": int(alert.profile_id),
            "opportunity_id": int(alert.opportunity_id) if alert.opportunity_id is not None else None,
            "external_source": alert.external_source,
            "external_opportunity_id": alert.external_opportunity_id,
            "title": alert.title,
            "status": alert.status,
            "matched_at": alert.matched_at.isoformat() if alert.matched_at else None,
            "details": alert.details_json or {},
        }
        for alert in alerts
    ]


def run_active_saved_search_alerts(*, xml_text: Optional[str] = None) -> dict[str, Any]:
    profiles = list(
        db.session.scalars(
            select(GrantSearchProfile).where(
                GrantSearchProfile.is_active == True,
                GrantSearchProfile.source == "grants_gov",
            )
        )
    )
    profile_runs = 0
    created_alerts = 0
    for profile in profiles:
        try:
            result = run_search_profile(int(profile.id), int(profile.organization_id), xml_text=xml_text)
            profile_runs += 1
            created_alerts += int(result.get("created_alerts", 0))
        except Exception as exc:
            logger.warning("grant search profile run failed: %s", exc)
    return {"profiles_run": profile_runs, "created_alerts": created_alerts}


_VALID_ALERT_STATUSES = frozenset({"new", "reviewed", "dismissed", "actioned"})


def acknowledge_search_alert(
    alert_id: int,
    organization_id: int,
    *,
    new_status: str = "reviewed",
    notes: Optional[str] = None,
) -> dict[str, Any]:
    """Transition a GrantSearchAlert to a user-acknowledged status.

    Allowed transitions from *new*: reviewed, dismissed, actioned.
    Allowed transition from *reviewed*: actioned, dismissed.
    """
    clean_status = str(new_status or "reviewed").strip().lower()
    if clean_status not in _VALID_ALERT_STATUSES:
        raise ValueError(f"new_status must be one of {sorted(_VALID_ALERT_STATUSES)}")

    alert = db.session.scalars(
        select(GrantSearchAlert).where(
            GrantSearchAlert.id == int(alert_id),
            GrantSearchAlert.organization_id == int(organization_id),
        ).limit(1)
    ).first()
    if alert is None:
        raise LookupError("alert not found for organization")

    alert.status = clean_status
    if notes is not None:
        details = dict(alert.details_json or {})
        details["acknowledge_notes"] = str(notes).strip()
        alert.details_json = details

    db.session.commit()
    audit(
        "grant.search_alert.acknowledge",
        entity_type="grant_search_alert",
        entity_id=int(alert.id),
        details={"organization_id": int(organization_id), "new_status": clean_status},
    )
    return {
        "id": int(alert.id),
        "status": alert.status,
        "title": alert.title,
        "external_opportunity_id": alert.external_opportunity_id,
    }
