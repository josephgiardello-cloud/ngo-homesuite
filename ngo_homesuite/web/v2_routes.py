"""JSON API routes for Grants, Tasks, Program Impact, Smart Groups, and P2P Fundraising.

All routes are prefixed with /api/v2 and require login.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from ngo_homesuite.web.rbac import roles_required

v2_bp = Blueprint("v2", __name__, url_prefix="/api/v2")


def _org_id() -> int:
    return int(current_user.organization_id)


def _json_or_400(required: list[str] | None = None) -> dict[str, Any]:
    data = request.get_json(silent=True) or {}
    if required:
        missing = [k for k in required if k not in data]
        if missing:
            from flask import abort
            abort(400, description=f"Missing required fields: {missing}")
    return data


def _parse_iso_date(value: str) -> date:
    return date.fromisoformat((value or "").strip())


# ------------------------------------------------------------------ #
# GRANTS
# ------------------------------------------------------------------ #

@v2_bp.route("/grants", methods=["GET"])
@login_required
def list_grants():
    from ngo_homesuite.services.grant_service import list_grants as svc_list
    status = request.args.get("status")
    grants = svc_list(_org_id(), status=status)
    return jsonify([_grant_dict(g) for g in grants])


@v2_bp.route("/grants", methods=["POST"])
@login_required
@roles_required("admin", "staff")
def create_grant():
    from ngo_homesuite.services.grant_service import create_grant as svc_create
    data = _json_or_400(required=["title", "funder_name"])
    try:
        grant = svc_create(_org_id(), **data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(_grant_dict(grant)), 201


@v2_bp.route("/grants/<int:grant_id>", methods=["GET"])
@login_required
def get_grant(grant_id: int):
    from ngo_homesuite.services.grant_service import get_grant as svc_get
    grant = svc_get(grant_id, _org_id())
    if not grant:
        return jsonify({"error": "not found"}), 404
    return jsonify(_grant_dict(grant))


@v2_bp.route("/grants/<int:grant_id>/advance", methods=["POST"])
@login_required
@roles_required("admin", "staff")
def advance_grant(grant_id: int):
    from ngo_homesuite.services.grant_service import advance_grant_status, GrantNotFound, InvalidGrantTransition
    data = _json_or_400(required=["new_status"])
    try:
        grant = advance_grant_status(
            grant_id,
            _org_id(),
            new_status=data["new_status"],
            **{k: v for k, v in data.items() if k != "new_status"},
        )
    except GrantNotFound as exc:
        return jsonify({"error": str(exc)}), 404
    except InvalidGrantTransition as exc:
        return jsonify({"error": str(exc)}), 422
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(_grant_dict(grant))


@v2_bp.route("/grants/<int:grant_id>/disbursements", methods=["POST"])
@login_required
@roles_required("admin", "staff")
def add_disbursement(grant_id: int):
    from ngo_homesuite.services.grant_service import add_disbursement as svc_add, GrantNotFound
    data = _json_or_400(required=["amount", "received_date"])
    payload = dict(data)
    try:
        payload["received_date"] = _parse_iso_date(str(payload["received_date"]))
    except ValueError:
        return jsonify({"error": "received_date must be ISO format YYYY-MM-DD"}), 400
    try:
        disb = svc_add(grant_id, _org_id(), **payload)
    except GrantNotFound as exc:
        return jsonify({"error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"id": disb.id, "amount": float(disb.amount), "received_date": str(disb.received_date)}), 201


@v2_bp.route("/grants/pipeline-summary", methods=["GET"])
@login_required
def grants_pipeline_summary():
    from ngo_homesuite.services.grant_service import grant_pipeline_summary
    return jsonify(grant_pipeline_summary(_org_id()))


def _grant_dict(g) -> dict:
    return {
        "id": g.id,
        "title": g.title,
        "funder_name": g.funder_name,
        "amount_requested": g.amount_requested,
        "amount_awarded": g.amount_awarded,
        "currency": g.currency,
        "status": g.status,
        "application_deadline": str(g.application_deadline) if g.application_deadline else None,
        "award_date": str(g.award_date) if g.award_date else None,
        "report_due_date": str(g.report_due_date) if g.report_due_date else None,
    }


# ------------------------------------------------------------------ #
# TASKS
# ------------------------------------------------------------------ #

@v2_bp.route("/tasks", methods=["GET"])
@login_required
def list_tasks():
    from ngo_homesuite.services.task_service import list_tasks as svc_list
    donor_id = request.args.get("donor_id", type=int)
    grant_id = request.args.get("grant_id", type=int)
    status = request.args.get("status")
    overdue = request.args.get("overdue") == "1"
    tasks = svc_list(_org_id(), donor_id=donor_id, grant_id=grant_id, status=status, overdue_only=overdue)
    return jsonify([_task_dict(t) for t in tasks])


@v2_bp.route("/tasks", methods=["POST"])
@login_required
@roles_required("admin", "staff")
def create_task():
    from ngo_homesuite.services.task_service import create_task as svc_create
    data = _json_or_400(required=["title"])
    task = svc_create(_org_id(), **data)
    return jsonify(_task_dict(task)), 201


@v2_bp.route("/tasks/<int:task_id>/complete", methods=["POST"])
@login_required
@roles_required("admin", "staff")
def complete_task(task_id: int):
    from ngo_homesuite.services.task_service import complete_task as svc_complete
    data = request.get_json(silent=True) or {}
    task = svc_complete(task_id, _org_id(), notes=data.get("notes"))
    return jsonify(_task_dict(task))


@v2_bp.route("/tasks/overdue-summary", methods=["GET"])
@login_required
def overdue_summary():
    from ngo_homesuite.services.task_service import overdue_task_summary
    return jsonify(overdue_task_summary(_org_id()))


def _task_dict(t) -> dict:
    return {
        "id": t.id,
        "title": t.title,
        "task_type": t.task_type,
        "priority": t.priority,
        "status": t.status,
        "due_date": t.due_date.isoformat() if t.due_date else None,
        "completed_at": t.completed_at.isoformat() if t.completed_at else None,
        "donor_id": t.donor_id,
        "grant_id": t.grant_id,
        "donation_id": t.donation_id,
        "assigned_to_id": t.assigned_to_id,
        "notes": t.notes,
    }


# ------------------------------------------------------------------ #
# PROGRAM CASES
# ------------------------------------------------------------------ #

@v2_bp.route("/cases", methods=["GET"])
@login_required
def list_cases():
    from ngo_homesuite.services.program_impact_service import list_cases as svc_list
    status = request.args.get("status")
    case_type = request.args.get("case_type")
    cases = svc_list(_org_id(), status=status, case_type=case_type)
    return jsonify([_case_dict(c) for c in cases])


@v2_bp.route("/cases", methods=["POST"])
@login_required
@roles_required("admin", "staff")
def create_case():
    from ngo_homesuite.services.program_impact_service import create_case as svc_create
    data = _json_or_400(required=["title"])
    case = svc_create(_org_id(), **data)
    return jsonify(_case_dict(case)), 201


@v2_bp.route("/cases/<int:case_id>", methods=["GET"])
@login_required
def get_case(case_id: int):
    from ngo_homesuite.services.program_impact_service import get_case as svc_get
    case = svc_get(case_id, _org_id())
    if not case:
        return jsonify({"error": "not found"}), 404
    return jsonify(_case_dict(case))


@v2_bp.route("/cases/<int:case_id>/status", methods=["POST"])
@login_required
@roles_required("admin", "staff")
def update_case_status(case_id: int):
    from ngo_homesuite.services.program_impact_service import update_case_status as svc_update
    data = _json_or_400(required=["new_status"])
    case = svc_update(case_id, _org_id(), **data)
    return jsonify(_case_dict(case))


@v2_bp.route("/cases/<int:case_id>/notes", methods=["POST"])
@login_required
@roles_required("admin", "staff")
def add_case_note(case_id: int):
    from ngo_homesuite.services.program_impact_service import add_note
    data = _json_or_400(required=["description"])
    activity = add_note(case_id, _org_id(), **data)
    return jsonify({"id": activity.id, "activity_type": activity.activity_type}), 201


@v2_bp.route("/cases/impact-report", methods=["GET"])
@login_required
def impact_report():
    from ngo_homesuite.services.program_impact_service import impact_report as svc_report
    case_type = request.args.get("case_type")
    return jsonify(svc_report(_org_id(), case_type=case_type))


def _case_dict(c) -> dict:
    return {
        "id": c.id,
        "title": c.title,
        "case_type": c.case_type,
        "status": c.status,
        "donor_id": c.donor_id,
        "project_id": c.project_id,
        "outcome_metric": c.outcome_metric,
        "outcome_value": c.outcome_value,
        "next_review_date": str(c.next_review_date) if c.next_review_date else None,
        "closed_date": str(c.closed_date) if c.closed_date else None,
    }


# ------------------------------------------------------------------ #
# SMART GROUPS
# ------------------------------------------------------------------ #

@v2_bp.route("/smart-groups", methods=["GET"])
@login_required
def list_smart_groups():
    from ngo_homesuite.services.smart_groups_service import list_groups
    groups = list_groups(_org_id())
    return jsonify([_group_dict(g) for g in groups])


@v2_bp.route("/smart-groups", methods=["POST"])
@login_required
@roles_required("admin", "staff")
def create_smart_group():
    from ngo_homesuite.services.smart_groups_service import create_group
    data = _json_or_400(required=["name", "rules"])
    group = create_group(_org_id(), **data)
    return jsonify(_group_dict(group)), 201


@v2_bp.route("/smart-groups/<int:group_id>/evaluate", methods=["POST"])
@login_required
@roles_required("admin", "staff")
def evaluate_smart_group(group_id: int):
    from ngo_homesuite.services.smart_groups_service import evaluate_group
    members = evaluate_group(group_id, _org_id())
    return jsonify({"count": len(members), "members": members[:200]})


def _group_dict(g) -> dict:
    return {
        "id": g.id,
        "name": g.name,
        "description": g.description,
        "rules": g.rules_json,
        "last_count": g.last_count,
        "last_evaluated_at": g.last_evaluated_at.isoformat() if g.last_evaluated_at else None,
    }


# ------------------------------------------------------------------ #
# P2P FUNDRAISING
# ------------------------------------------------------------------ #

@v2_bp.route("/p2p/pages", methods=["GET"])
@login_required
def list_p2p_pages():
    from ngo_homesuite.services.p2p_service import list_pages
    status = request.args.get("status")
    pages = list_pages(_org_id(), status=status)
    return jsonify([_p2p_dict(p) for p in pages])


@v2_bp.route("/p2p/pages", methods=["POST"])
@login_required
@roles_required("admin", "staff")
def create_p2p_page():
    from ngo_homesuite.services.p2p_service import create_page
    data = _json_or_400(required=["donor_id", "title"])
    try:
        page = create_page(_org_id(), **data)
    except ValueError:
        return jsonify({"error": "Invalid resource reference"}), 400

    return jsonify(_p2p_dict(page)), 201


@v2_bp.route("/p2p/pages/<int:page_id>", methods=["GET"])
@login_required
def get_p2p_page(page_id: int):
    from ngo_homesuite.services.p2p_service import get_page
    page = get_page(page_id, _org_id())
    if not page:
        return jsonify({"error": "not found"}), 404
    return jsonify(_p2p_dict(page))


@v2_bp.route("/p2p/pages/<int:page_id>/publish", methods=["POST"])
@login_required
@roles_required("admin", "staff")
def publish_p2p_page(page_id: int):
    from ngo_homesuite.services.p2p_service import publish_page
    page = publish_page(page_id, _org_id())
    return jsonify(_p2p_dict(page))


@v2_bp.route("/p2p/pages/<int:page_id>/progress", methods=["GET"])
@login_required
def p2p_progress(page_id: int):
    from ngo_homesuite.services.p2p_service import get_progress
    return jsonify(get_progress(page_id, _org_id()))


@v2_bp.route("/p2p/pages/<int:page_id>/link-donation", methods=["POST"])
@login_required
@roles_required("admin", "staff")
def link_p2p_donation(page_id: int):
    from ngo_homesuite.services.p2p_service import link_donation
    data = _json_or_400(required=["donation_id"])
    try:
        link = link_donation(page_id, _org_id(), int(data["donation_id"]))
    except ValueError:
        return jsonify({"error": "Invalid resource reference"}), 400

    return jsonify({"page_id": link.page_id, "donation_id": link.donation_id}), 201


@v2_bp.route("/p2p/leaderboard", methods=["GET"])
@login_required
def p2p_leaderboard():
    from ngo_homesuite.services.p2p_service import leaderboard
    return jsonify(
        leaderboard(
            _org_id(),
            limit=request.args.get("limit", 10, type=int),
            offset=request.args.get("offset", 0, type=int),
        )
    )


def _p2p_dict(p) -> dict:
    return {
        "id": p.id,
        "title": p.title,
        "public_slug": p.public_slug,
        "status": p.status,
        "goal_amount": p.goal_amount,
        "donor_id": p.donor_id,
        "campaign_slug": p.campaign_slug,
    }


# ------------------------------------------------------------------ #
# ENGAGEMENT SCORES
# ------------------------------------------------------------------ #

@v2_bp.route("/donors/<int:donor_id>/engagement-score", methods=["GET"])
@login_required
def get_engagement_score(donor_id: int):
    from ngo_homesuite.services.engagement_scoring_service import compute_score, get_score
    rec = get_score(_org_id(), donor_id) or compute_score(_org_id(), donor_id)
    return jsonify({
        "donor_id": rec.donor_id,
        "score": float(rec.score),
        "segment": rec.segment,
        "cultivation_priority": rec.cultivation_priority,
        "explanation": rec.explanation,
        "breakdown": {
            "recency": float(rec.recency_score),
            "frequency": float(rec.frequency_score),
            "monetary": float(rec.monetary_score),
            "engagement": float(rec.engagement_score),
        },
    })


@v2_bp.route("/engagement-scores/batch-recompute", methods=["POST"])
@login_required
@roles_required("admin")
def batch_recompute_scores():
    from ngo_homesuite.services.engagement_scoring_service import batch_recompute
    return jsonify(batch_recompute(_org_id()))


@v2_bp.route("/engagement-scores/at-risk", methods=["GET"])
@login_required
def at_risk_donors():
    from ngo_homesuite.services.engagement_scoring_service import high_priority_lapsed
    limit = request.args.get("limit", 20, type=int)
    records = high_priority_lapsed(_org_id(), limit=limit)
    return jsonify([
        {
            "donor_id": r.donor_id,
            "score": float(r.score),
            "segment": r.segment,
            "priority": r.cultivation_priority,
        }
        for r in records
    ])


# ------------------------------------------------------------------ #
# MEMBERSHIPS
# ------------------------------------------------------------------ #

@v2_bp.route("/membership/tiers", methods=["GET"])
@login_required
def list_tiers():
    from ngo_homesuite.services.membership_service import list_tiers as svc
    return jsonify([_tier_dict(t) for t in svc(_org_id())])


@v2_bp.route("/membership/tiers", methods=["POST"])
@login_required
@roles_required("admin", "staff")
def create_tier():
    from ngo_homesuite.services.membership_service import create_tier as svc
    data = _json_or_400(required=["name", "price"])
    tier = svc(_org_id(), **data)
    return jsonify(_tier_dict(tier)), 201


@v2_bp.route("/membership/enroll", methods=["POST"])
@login_required
@roles_required("admin", "staff")
def enroll_member():
    from ngo_homesuite.services.membership_service import enroll_member as svc
    data = _json_or_400(required=["donor_id", "tier_id"])
    record = svc(_org_id(), **data)
    return jsonify({"id": record.id, "status": record.status, "end_date": str(record.end_date)}), 201


@v2_bp.route("/membership/summary", methods=["GET"])
@login_required
def membership_summary():
    from ngo_homesuite.services.membership_service import membership_summary as svc
    return jsonify(svc(_org_id()))


def _tier_dict(t) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "price": float(t.price),
        "interval": t.interval,
        "benefits": t.benefits,
        "is_active": bool(t.is_active),
    }


# ------------------------------------------------------------------ #
# ACTIVITY TIMELINES (Unified Constituent Activity Feed)
# ------------------------------------------------------------------ #

@v2_bp.route("/activity/donor/<int:donor_id>", methods=["GET"])
@login_required
def get_donor_activity_timeline(donor_id: int):
    """Unified timeline for a donor including interactions, donations, pledges."""
    from ngo_homesuite.services.activity_timeline_service import ActivityTimelineService

    limit = request.args.get("limit", 100, type=int)
    offset = request.args.get("offset", 0, type=int)

    # Validate pagination bounds
    limit = min(limit, 500)
    offset = max(offset, 0)

    items = ActivityTimelineService.get_donor_timeline(_org_id(), donor_id, limit=limit, offset=offset)
    return jsonify([item.to_dict() for item in items])


@v2_bp.route("/activity/beneficiary/<int:beneficiary_id>", methods=["GET"])
@login_required
def get_beneficiary_activity_timeline(beneficiary_id: int):
    """Unified timeline for a beneficiary including case notes, service logs, appointments."""
    from ngo_homesuite.services.activity_timeline_service import ActivityTimelineService

    limit = request.args.get("limit", 100, type=int)
    offset = request.args.get("offset", 0, type=int)

    # Validate pagination bounds
    limit = min(limit, 500)
    offset = max(offset, 0)

    items = ActivityTimelineService.get_beneficiary_timeline(_org_id(), beneficiary_id, limit=limit, offset=offset)
    return jsonify([item.to_dict() for item in items])


@v2_bp.route("/activity/global", methods=["GET"])
@login_required
def get_organization_activity_feed():
    """Organization-wide activity feed for dashboard (all interactions, donations, key events)."""
    from ngo_homesuite.services.activity_timeline_service import ActivityTimelineService

    limit = request.args.get("limit", 50, type=int)
    offset = request.args.get("offset", 0, type=int)
    entity_type = request.args.get("entity_type")  # Optional: "donor", "beneficiary", etc.

    # Validate pagination bounds
    limit = min(limit, 500)
    offset = max(offset, 0)

    items = ActivityTimelineService.get_organization_activity(
        _org_id(),
        limit=limit,
        offset=offset,
        entity_type_filter=entity_type,
    )
    return jsonify([item.to_dict() for item in items])
