"""JSON API routes for Grants, Tasks, Program Impact, Smart Groups, and P2P Fundraising.

All routes are prefixed with /api/v2 and require login.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required
import requests
from sqlalchemy import select

from ngo_homesuite.grants.facade import GrantsFacade
from ngo_homesuite.grants.exceptions import GrantNotFound, InvalidGrantTransition
from ngo_homesuite.models.core import Donor, Grant, User, db

from ngo_homesuite.web.rbac import roles_required

v2_bp = Blueprint("v2", __name__, url_prefix="/api/v2")
_GRANTS_FACADE = GrantsFacade()


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


def _grants():
    return _GRANTS_FACADE


def _normalize_grant_dates(data: dict[str, Any], fields: tuple[str, ...]) -> tuple[dict[str, Any], str | None]:
    payload = dict(data)
    for field in fields:
        if field not in payload or payload[field] in (None, ""):
            continue
        if isinstance(payload[field], date):
            continue
        try:
            payload[field] = _parse_iso_date(str(payload[field]))
        except ValueError:
            return payload, f"{field} must be ISO format YYYY-MM-DD"
    return payload, None


# ------------------------------------------------------------------ #
# GRANTS
# ------------------------------------------------------------------ #

@v2_bp.route("/grants", methods=["GET"])
@login_required
def list_grants():
    status = request.args.get("status")
    grants = _grants().list_grants(_org_id(), status=status)
    return jsonify([_grant_dict(g) for g in grants])


@v2_bp.route("/grants", methods=["POST"])
@login_required
@roles_required("admin", "staff")
def create_grant():
    data = _json_or_400(required=["title", "funder_name"])
    payload, error = _normalize_grant_dates(
        data,
        ("application_deadline", "submission_date", "award_date", "start_date", "end_date", "report_due_date"),
    )
    if error:
        return jsonify({"error": error}), 400
    try:
        grant = _grants().create_grant(_org_id(), **payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(_grant_dict(grant)), 201


@v2_bp.route("/grants/<int:grant_id>", methods=["GET"])
@login_required
def get_grant(grant_id: int):
    grant = _grants().get_grant(grant_id, _org_id())
    if not grant:
        return jsonify({"error": "not found"}), 404
    return jsonify(_grant_dict(grant))


@v2_bp.route("/grants/<int:grant_id>/advance", methods=["POST"])
@login_required
@roles_required("admin", "staff")
def advance_grant(grant_id: int):
    data = _json_or_400(required=["new_status"])
    payload, error = _normalize_grant_dates(data, ("submission_date", "award_date", "report_due_date"))
    if error:
        return jsonify({"error": error}), 400
    try:
        grant = _grants().advance_grant_status(
            grant_id,
            _org_id(),
            new_status=payload["new_status"],
            **{k: v for k, v in payload.items() if k != "new_status"},
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
    data = _json_or_400(required=["amount", "received_date"])
    payload = dict(data)
    try:
        payload["received_date"] = _parse_iso_date(str(payload["received_date"]))
    except ValueError:
        return jsonify({"error": "received_date must be ISO format YYYY-MM-DD"}), 400
    try:
        disb = _grants().add_disbursement(grant_id, _org_id(), **payload)
    except GrantNotFound as exc:
        return jsonify({"error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"id": disb.id, "amount": float(disb.amount), "received_date": str(disb.received_date)}), 201


@v2_bp.route("/grants/pipeline-summary", methods=["GET"])
@login_required
def grants_pipeline_summary():
    return jsonify(_grants().grant_pipeline_summary(_org_id()))


@v2_bp.route("/grants/calendar", methods=["GET"])
@login_required
def grants_calendar():
    within_days = request.args.get("within_days", 120, type=int)
    return jsonify(_grants().grant_calendar_events(_org_id(), within_days=max(1, min(within_days, 730))))


@v2_bp.route("/grants/restricted-funds", methods=["GET"])
@login_required
def grants_restricted_funds():
    return jsonify(_grants().restricted_funding_summary(_org_id()))


@v2_bp.route("/grants/opportunities/calibrate", methods=["POST"])
@login_required
@roles_required("admin", "staff")
def calibrate_external_grant_opportunity():
    data = _json_or_400(required=["source", "payload"])
    source = str(data.get("source") or "").strip().lower()
    payload = data.get("payload")
    if not isinstance(payload, dict):
        return jsonify({"error": "payload must be an object"}), 400
    try:
        result = _grants().calibrate_external_opportunity(source, payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)


@v2_bp.route("/grants/opportunities/import/grants-gov", methods=["POST"])
@login_required
@roles_required("admin", "staff")
def import_grants_gov_opportunities_route():
    data = request.get_json(silent=True) or {}

    # Direct payload mode supports deterministic testing and one-off imports.
    raw_records = data.get("records")
    if raw_records is not None:
        if not isinstance(raw_records, list):
            return jsonify({"error": "records must be a list"}), 400
        imported_ids: list[int] = []
        calibration_failures: list[dict[str, object]] = []
        probability = float(data.get("probability", 0.4))
        status = str(data.get("status", "identified"))
        for record in raw_records:
            if not isinstance(record, dict):
                calibration_failures.append({"external_id": None, "missing_fields": ["payload"], "score": 0.0})
                continue
            calibration = _grants().calibrate_external_opportunity("grants_gov", record)
            if not calibration.get("is_ready"):
                calibration_failures.append(
                    {
                        "external_id": calibration.get("normalized_preview", {}).get("external_id"),
                        "missing_fields": calibration.get("missing_fields", []),
                        "score": calibration.get("score", 0.0),
                    }
                )
                continue
            imported = _grants().import_external_opportunity(
                _org_id(),
                source="grants_gov",
                payload=record,
                probability=probability,
                status=status,
            )
            imported_ids.append(int(imported.id))
        return jsonify(
            {
                "source": "grants_gov",
                "mode": "records",
                "fetched": len(raw_records),
                "imported": len(imported_ids),
                "calibration_failures": calibration_failures,
                "opportunity_ids": imported_ids,
            }
        )

    try:
        result = _grants().import_grants_gov_opportunities(
            _org_id(),
            keyword=data.get("keyword"),
            rows=int(data.get("rows", 25)),
            status=str(data.get("status", "identified")),
            probability=float(data.get("probability", 0.4)),
            endpoint=data.get("endpoint"),
            timeout_seconds=int(data.get("timeout_seconds", 25)),
        )
    except requests.RequestException as exc:
        return jsonify({"error": f"grants.gov request failed: {exc}"}), 502
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)


def _grant_dict(g) -> dict:
    return {
        "id": g.id,
        "title": g.title,
        "funder_name": g.funder_name,
        "funder_type": g.funder_type,
        "funder_contact": g.funder_contact,
        "funder_email": g.funder_email,
        "amount_requested": g.amount_requested,
        "amount_awarded": g.amount_awarded,
        "currency": g.currency,
        "status": g.status,
        "application_deadline": str(g.application_deadline) if g.application_deadline else None,
        "submission_date": str(g.submission_date) if g.submission_date else None,
        "award_date": str(g.award_date) if g.award_date else None,
        "start_date": str(g.start_date) if g.start_date else None,
        "end_date": str(g.end_date) if g.end_date else None,
        "report_due_date": str(g.report_due_date) if g.report_due_date else None,
        "requirements": g.requirements,
        "notes": g.notes,
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
    project_id = request.args.get("project_id", type=int)
    donation_id = request.args.get("donation_id", type=int)
    assigned_to_id = request.args.get("assigned_to_id", type=int)
    task_type = request.args.get("task_type")
    status = request.args.get("status")
    priority = request.args.get("priority")
    overdue = request.args.get("overdue") == "1"
    due_within_days = request.args.get("due_within_days", type=int)
    tasks = svc_list(
        _org_id(),
        donor_id=donor_id,
        grant_id=grant_id,
        project_id=project_id,
        donation_id=donation_id,
        assigned_to_id=assigned_to_id,
        task_type=task_type,
        status=status,
        priority=priority,
        overdue_only=overdue,
        due_within_days=due_within_days,
    )
    labels = _task_labels(_org_id(), tasks)
    return jsonify([_task_dict(t, labels=labels) for t in tasks])


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


@v2_bp.route("/tasks/board", methods=["GET"])
@login_required
def task_board():
    from ngo_homesuite.services.reminder_service import recommend_task_reminders
    from ngo_homesuite.services.task_service import task_board_snapshot

    donor_id = request.args.get("donor_id", type=int)
    grant_id = request.args.get("grant_id", type=int)
    project_id = request.args.get("project_id", type=int)
    donation_id = request.args.get("donation_id", type=int)
    assigned_to_id = request.args.get("assigned_to_id", type=int)
    status = request.args.get("status")
    priority = request.args.get("priority")
    reminders_limit = request.args.get("reminders_limit", 20, type=int)

    board = task_board_snapshot(
        _org_id(),
        donor_id=donor_id,
        grant_id=grant_id,
        project_id=project_id,
        donation_id=donation_id,
        assigned_to_id=assigned_to_id,
        status=status,
        priority=priority,
    )
    tasks = board["tasks"]
    labels = _task_labels(_org_id(), tasks)
    reminders = recommend_task_reminders(
        _org_id(),
        limit=max(1, min(reminders_limit, 100)),
        task_ids=[t.id for t in tasks],
    )

    return jsonify(
        {
            "summary": board["summary"],
            "tasks": [_task_dict(t, labels=labels) for t in tasks],
            "reminder_candidates": reminders,
        }
    )


@v2_bp.route("/tasks/reminder-candidates", methods=["GET"])
@login_required
def task_reminder_candidates():
    from ngo_homesuite.services.reminder_service import recommend_task_reminders

    limit = request.args.get("limit", 25, type=int)
    payload = recommend_task_reminders(_org_id(), limit=max(1, min(limit, 200)))
    return jsonify(payload)


def _task_labels(org_id: int, tasks) -> dict[str, dict[int, str]]:
    donor_ids = sorted({int(t.donor_id) for t in tasks if t.donor_id})
    grant_ids = sorted({int(t.grant_id) for t in tasks if t.grant_id})
    user_ids = sorted({int(t.assigned_to_id) for t in tasks if t.assigned_to_id})

    donor_map: dict[int, str] = {}
    grant_map: dict[int, str] = {}
    user_map: dict[int, str] = {}

    if donor_ids:
        for donor in db.session.scalars(select(Donor).where(Donor.organization_id == org_id, Donor.id.in_(donor_ids))):
            donor_map[int(donor.id)] = donor.name

    if grant_ids:
        for grant in db.session.scalars(select(Grant).where(Grant.organization_id == org_id, Grant.id.in_(grant_ids))):
            grant_map[int(grant.id)] = grant.title

    if user_ids:
        for user in db.session.scalars(select(User).where(User.id.in_(user_ids))):
            display = ((user.first_name or "").strip() + " " + (user.last_name or "").strip()).strip() or user.username
            user_map[int(user.id)] = display

    return {
        "donor": donor_map,
        "grant": grant_map,
        "user": user_map,
    }


def _task_dict(t, *, labels: dict[str, dict[int, str]] | None = None) -> dict:
    labels = labels or {"donor": {}, "grant": {}, "user": {}}
    return {
        "id": t.id,
        "title": t.title,
        "description": t.description,
        "task_type": t.task_type,
        "priority": t.priority,
        "status": t.status,
        "due_date": t.due_date.isoformat() if t.due_date else None,
        "completed_at": t.completed_at.isoformat() if t.completed_at else None,
        "donor_id": t.donor_id,
        "donor_name": labels["donor"].get(int(t.donor_id)) if t.donor_id else None,
        "grant_id": t.grant_id,
        "grant_title": labels["grant"].get(int(t.grant_id)) if t.grant_id else None,
        "project_id": t.project_id,
        "donation_id": t.donation_id,
        "assigned_to_id": t.assigned_to_id,
        "assigned_to_name": labels["user"].get(int(t.assigned_to_id)) if t.assigned_to_id else None,
        "reminder_channel": t.reminder_channel,
        "reminder_sent_count": t.reminder_sent_count,
        "last_reminder_sent_at": t.last_reminder_sent_at.isoformat() if t.last_reminder_sent_at else None,
        "last_reminder_error": t.last_reminder_error,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
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
    search_query = (request.args.get("q") or "").strip() or None

    # Validate pagination bounds
    limit = min(limit, 500)
    offset = max(offset, 0)

    items = ActivityTimelineService.get_donor_timeline(
        _org_id(),
        donor_id,
        limit=limit,
        offset=offset,
        search_query=search_query,
    )
    return jsonify([item.to_dict() for item in items])


@v2_bp.route("/activity/beneficiary/<int:beneficiary_id>", methods=["GET"])
@login_required
def get_beneficiary_activity_timeline(beneficiary_id: int):
    """Unified timeline for a beneficiary including case notes, service logs, appointments."""
    from ngo_homesuite.services.activity_timeline_service import ActivityTimelineService

    limit = request.args.get("limit", 100, type=int)
    offset = request.args.get("offset", 0, type=int)
    search_query = (request.args.get("q") or "").strip() or None

    # Validate pagination bounds
    limit = min(limit, 500)
    offset = max(offset, 0)

    items = ActivityTimelineService.get_beneficiary_timeline(
        _org_id(),
        beneficiary_id,
        limit=limit,
        offset=offset,
        search_query=search_query,
    )
    return jsonify([item.to_dict() for item in items])


@v2_bp.route("/activity/global", methods=["GET"])
@login_required
def get_organization_activity_feed():
    """Organization-wide activity feed for dashboard (all interactions, donations, key events)."""
    from ngo_homesuite.services.activity_timeline_service import ActivityTimelineService

    limit = request.args.get("limit", 50, type=int)
    offset = request.args.get("offset", 0, type=int)
    entity_type = request.args.get("entity_type")  # Optional: "donor", "beneficiary", etc.
    activity_type = request.args.get("activity_type")
    search_query = (request.args.get("q") or "").strip() or None

    # Validate pagination bounds
    limit = min(limit, 500)
    offset = max(offset, 0)

    items = ActivityTimelineService.get_organization_activity(
        _org_id(),
        limit=limit,
        offset=offset,
        entity_type_filter=entity_type,
        activity_type_filter=activity_type,
        search_query=search_query,
    )
    return jsonify([item.to_dict() for item in items])


@v2_bp.route("/activity/insights", methods=["GET"])
@login_required
def get_activity_insights():
    """AI Copilot summary + suggested next actions for the current activity feed context."""
    from ngo_homesuite.ai.copilot_tools import CopilotToolRegistry

    limit = request.args.get("limit", 40, type=int)
    entity_type = request.args.get("entity_type")
    activity_type = request.args.get("activity_type")
    search_query = (request.args.get("q") or "").strip() or None

    payload = CopilotToolRegistry().execute(
        "summarize_activity_timeline",
        {
            "limit": max(1, min(limit, 100)),
            "entity_type": entity_type,
            "activity_type": activity_type,
            "query": search_query,
        },
        {
            "organization_id": _org_id(),
            "actor": getattr(current_user, "username", "web"),
        },
    )
    return jsonify(payload)


# ------------------------------------------------------------------ #
# TASK REMINDERS & MANAGEMENT
# ------------------------------------------------------------------ #

@v2_bp.route("/tasks/my", methods=["GET"])
@login_required
def my_tasks():
    """Get tasks assigned to current user."""
    from ngo_homesuite.services.task_service import list_tasks as svc_list
    
    status = request.args.get("status")
    priority = request.args.get("priority")
    overdue_only = request.args.get("overdue") == "1"
    
    tasks = svc_list(
        _org_id(),
        assigned_to_id=current_user.id,
        status=status,
        priority=priority,
        overdue_only=overdue_only,
    )
    return jsonify([_task_dict(t) for t in tasks])


@v2_bp.route("/tasks/<int:task_id>", methods=["PATCH"])
@login_required
@roles_required("admin", "staff")
def update_task(task_id: int):
    """Update task (status, assignment, reminder channel)."""
    from ngo_homesuite.services.task_service import update_task as svc_update, get_task as svc_get
    
    data = request.get_json(silent=True) or {}
    task = svc_update(task_id, _org_id(), **data)
    return jsonify(_task_dict(task))


@v2_bp.route("/tasks/<int:task_id>/remind", methods=["POST"])
@login_required
@roles_required("admin", "staff")
def send_task_reminder(task_id: int):
    """Manually send reminder for a task."""
    from ngo_homesuite.services.reminder_service import dispatch_task_reminder
    
    data = request.get_json(silent=True) or {}
    reminder_type = data.get("reminder_type", "manual")
    
    result = dispatch_task_reminder(task_id, _org_id(), reminder_type=reminder_type)
    return jsonify(result)


@v2_bp.route("/tasks/reminders", methods=["GET"])
@login_required
def task_reminder_history():
    """Get reminder history for tasks."""
    from ngo_homesuite.services.reminder_service import list_reminders
    
    task_id = request.args.get("task_id", type=int)
    delivery_status = request.args.get("delivery_status")
    
    reminders = list_reminders(_org_id(), task_id=task_id, delivery_status=delivery_status)
    return jsonify([
        {
            "id": r.id,
            "task_id": r.task_id,
            "sent_to_user_id": r.sent_to_user_id,
            "channel": r.channel,
            "reminder_type": r.reminder_type,
            "sent_at": r.sent_at.isoformat(),
            "delivery_status": r.delivery_status,
            "delivery_error": r.delivery_error,
        }
        for r in reminders
    ])


@v2_bp.route("/tasks/dispatch-reminders", methods=["POST"])
@login_required
@roles_required("admin")
def dispatch_reminders_admin():
    """Admin endpoint to manually dispatch task reminders (for testing/adhoc)."""
    from ngo_homesuite.services.reminder_service import (
        dispatch_upcoming_task_reminders,
        dispatch_overdue_task_reminders,
    )
    
    data = request.get_json(silent=True) or {}
    reminder_type = data.get("type", "upcoming")  # "upcoming", "overdue", or "both"
    
    result = {}
    if reminder_type in ("upcoming", "both"):
        hours_before = data.get("hours_before", 24)
        result["upcoming"] = dispatch_upcoming_task_reminders(_org_id(), hours_before_due=hours_before)
    
    if reminder_type in ("overdue", "both"):
        result["overdue"] = dispatch_overdue_task_reminders(_org_id())
    
    return jsonify(result)
