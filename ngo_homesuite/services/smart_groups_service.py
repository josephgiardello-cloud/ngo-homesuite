"""Smart Groups / Dynamic Audiences service.

Rules engine that evaluates live data against saved rule sets to produce
auto-updating donor lists.

Rule schema (stored as JSON in SmartGroup.rules_json):
    [
        {"field": "segment", "op": "eq", "value": "lybunt"},
        {"field": "score",   "op": "gte", "value": 70},
        {"field": "donor_type", "op": "eq", "value": "individual"},
        {"field": "membership_status", "op": "eq", "value": "active"},
        {"field": "last_gift_days_ago", "op": "lte", "value": 365},
        {"field": "total_giving", "op": "gte", "value": 500},
    ]

Supported fields:
    segment              — engagement segment (champion, loyal, at_risk, lapsed, new, promising)
    score                — engagement score 0–100
    donor_type           — individual / corporate / foundation / anonymous
    membership_status    — active / lapsed / cancelled / none
    last_gift_days_ago   — integer
    total_giving         — float (lifetime)
    gift_count           — integer (lifetime)

Supported ops: eq, neq, gt, gte, lt, lte, in, not_in
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func

from ngo_homesuite.models.core import (
    Donation,
    Donor,
    DonorEngagementScore,
    MembershipRecord,
    SmartGroup,
    db,
)

logger = logging.getLogger(__name__)


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Group CRUD
# ---------------------------------------------------------------------------

def create_group(
    organization_id: int,
    name: str,
    rules: List[Dict[str, Any]],
    description: Optional[str] = None,
) -> SmartGroup:
    _validate_rules(rules)
    group = SmartGroup(
        organization_id=organization_id,
        name=name,
        description=description,
        rules_json=rules,
    )
    db.session.add(group)
    db.session.commit()
    return group


def update_group(
    group_id: int, organization_id: int, *, name: Optional[str] = None,
    rules: Optional[List[Dict[str, Any]]] = None, description: Optional[str] = None,
) -> SmartGroup:
    group = SmartGroup.query.filter_by(id=group_id, organization_id=organization_id).first_or_404()
    if name:
        group.name = name
    if description is not None:
        group.description = description
    if rules is not None:
        _validate_rules(rules)
        group.rules_json = rules
    db.session.commit()
    return group


def delete_group(group_id: int, organization_id: int) -> None:
    group = SmartGroup.query.filter_by(id=group_id, organization_id=organization_id).first_or_404()
    db.session.delete(group)
    db.session.commit()


def list_groups(organization_id: int) -> List[SmartGroup]:
    return SmartGroup.query.filter_by(organization_id=organization_id, is_active=True).all()


# ---------------------------------------------------------------------------
# Rule validation
# ---------------------------------------------------------------------------

VALID_FIELDS = {
    "segment", "score", "donor_type", "membership_status",
    "last_gift_days_ago", "total_giving", "gift_count",
}
VALID_OPS = {"eq", "neq", "gt", "gte", "lt", "lte", "in", "not_in"}


def _validate_rules(rules: List[Dict[str, Any]]) -> None:
    for r in rules:
        if r.get("field") not in VALID_FIELDS:
            raise ValueError(f"Unknown rule field: {r.get('field')}")
        if r.get("op") not in VALID_OPS:
            raise ValueError(f"Unknown rule operator: {r.get('op')}")


# ---------------------------------------------------------------------------
# Evaluation engine
# ---------------------------------------------------------------------------

def _matches(value: Any, op: str, target: Any) -> bool:
    try:
        if op == "eq":
            return value == target
        if op == "neq":
            return value != target
        if op == "gt":
            return float(value) > float(target)
        if op == "gte":
            return float(value) >= float(target)
        if op == "lt":
            return float(value) < float(target)
        if op == "lte":
            return float(value) <= float(target)
        if op == "in":
            return value in (target if isinstance(target, (list, tuple)) else [target])
        if op == "not_in":
            return value not in (target if isinstance(target, (list, tuple)) else [target])
    except (TypeError, ValueError):
        return False
    return False


def _build_donor_facts(
    donor: Donor,
    org_id: int,
    score_map: Dict[int, DonorEngagementScore],
    membership_map: Dict[int, str],
    giving_map: Dict[int, Dict],
) -> Dict[str, Any]:
    score_rec = score_map.get(donor.id)
    mem_status = membership_map.get(donor.id, "none")
    giving = giving_map.get(donor.id, {})
    last_gift = giving.get("last_gift")
    days_ago = ((_today() - last_gift.date() if hasattr(last_gift, "date") else last_gift) if last_gift else None)
    if days_ago is not None and not isinstance(days_ago, int):
        days_ago = days_ago.days

    return {
        "segment": score_rec.segment if score_rec else "unknown",
        "score": float(score_rec.score) if score_rec else 0.0,
        "donor_type": donor.donor_type or "individual",
        "membership_status": mem_status,
        "last_gift_days_ago": days_ago if days_ago is not None else 99999,
        "total_giving": float(giving.get("total", 0)),
        "gift_count": int(giving.get("count", 0)),
    }


def evaluate_group(group_id: int, organization_id: int) -> List[Dict[str, Any]]:
    """Return list of matching donor dicts and update last_count."""
    group = SmartGroup.query.filter_by(id=group_id, organization_id=organization_id).first_or_404()
    rules = group.rules_json if isinstance(group.rules_json, list) else json.loads(group.rules_json)

    donors = Donor.query.filter_by(organization_id=organization_id).all()

    # Build lookup maps (one DB round-trip each)
    score_records = DonorEngagementScore.query.filter_by(organization_id=organization_id).all()
    score_map = {s.donor_id: s for s in score_records}

    mem_records = MembershipRecord.query.filter(
        MembershipRecord.organization_id == organization_id,
        MembershipRecord.status == "active",
    ).all()
    membership_map = {m.donor_id: m.status for m in mem_records}

    giving_rows = (
        db.session.query(
            Donation.donor_id,
            func.sum(Donation.amount).label("total"),
            func.count(Donation.id).label("count"),
            func.max(Donation.donation_date).label("last_gift"),
        )
        .filter(
            Donation.organization_id == organization_id,
            Donation.donor_id.isnot(None),
        )
        .group_by(Donation.donor_id)
        .all()
    )
    giving_map = {
        r.donor_id: {"total": r.total, "count": r.count, "last_gift": r.last_gift}
        for r in giving_rows
    }

    results = []
    for donor in donors:
        facts = _build_donor_facts(donor, organization_id, score_map, membership_map, giving_map)
        if all(_matches(facts.get(rule["field"]), rule["op"], rule["value"]) for rule in rules):
            results.append(
                {
                    "donor_id": donor.id,
                    "name": donor.name,
                    "email": donor.email,
                    "phone": donor.phone,
                    "score": facts["score"],
                    "segment": facts["segment"],
                    "membership_status": facts["membership_status"],
                    "total_giving": facts["total_giving"],
                    "last_gift_days_ago": facts["last_gift_days_ago"],
                }
            )

    # Persist count and timestamp
    group.last_count = len(results)
    group.last_evaluated_at = _utcnow()
    db.session.commit()

    return results


def evaluate_all_groups(organization_id: int) -> Dict[str, Any]:
    """Re-evaluate all active groups and return summary."""
    groups = list_groups(organization_id)
    results = {}
    for g in groups:
        try:
            members = evaluate_group(g.id, organization_id)
            results[g.name] = {"count": len(members), "id": g.id}
        except Exception as exc:  # noqa: BLE001
            logger.error("Group eval failed group=%s: %s", g.id, exc)
            results[g.name] = {"error": str(exc)}
    return results
