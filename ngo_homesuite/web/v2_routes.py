"""JSON API routes for Grants, Tasks, Program Impact, Smart Groups, and P2P Fundraising.

All routes are prefixed with /api/v2 and require login.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any
import uuid

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user, login_required
from sqlalchemy import select
from werkzeug.utils import secure_filename

from ngo_homesuite.grants.facade import GrantsFacade
from ngo_homesuite.grants.exceptions import GrantApprovalError, GrantNotFound, InvalidGrantTransition
from ngo_homesuite.models.core import Donor, Grant, User, db

from ngo_homesuite.web.rbac import roles_required

v2_bp = Blueprint("v2", __name__, url_prefix="/api/v2")
_GRANTS_FACADE = GrantsFacade()
_PHOTO_ALLOWED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}
_PHOTO_MAX_BYTES = 5 * 1024 * 1024


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


def _campaign_photo_url(campaign_id: int, photo_path: str | None) -> str | None:
    if not photo_path:
        return None
    return f"/media/campaigns/{int(campaign_id)}/photo"


def _save_campaign_photo_upload(uploaded, *, org_id: int, campaign_id: int) -> str:
    if uploaded is None or not getattr(uploaded, 'filename', None):
        raise ValueError('No file uploaded')

    filename = secure_filename(str(uploaded.filename or ''))
    if not filename:
        raise ValueError('Invalid file name')

    ext = Path(filename).suffix.lower()
    if ext not in _PHOTO_ALLOWED_EXTENSIONS:
        raise ValueError('Unsupported image type. Allowed: .jpg, .jpeg, .png, .gif, .webp')

    uploaded.stream.seek(0, 2)
    size_bytes = uploaded.stream.tell()
    uploaded.stream.seek(0)
    if size_bytes > _PHOTO_MAX_BYTES:
        raise ValueError('Image must be 5MB or smaller')

    target_dir = Path(current_app.instance_path) / 'uploads' / 'campaigns' / f'org_{int(org_id)}'
    target_dir.mkdir(parents=True, exist_ok=True)
    target_name = f"{int(campaign_id)}-{uuid.uuid4().hex}{ext}"
    target_path = target_dir / target_name
    uploaded.save(target_path)
    return str((Path('uploads') / 'campaigns' / f'org_{int(org_id)}' / target_name).as_posix())


def _human_in_the_loop_metadata(data: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    compliance = data.get("compliance") if isinstance(data.get("compliance"), dict) else {}
    ai_assisted = bool(compliance.get("ai_assisted", False))
    contains_internal_details = bool(compliance.get("contains_internal_details", False))
    required = True

    reviewer_name = str(compliance.get("reviewer_name") or "").strip()
    reviewer_role = str(compliance.get("reviewer_role") or "").strip()
    warning_acknowledged = bool(compliance.get("warning_acknowledged", False))
    human_confirmation_text = str(compliance.get("human_confirmation_text") or "").strip()

    metadata = {
        "required": required,
        "ai_assisted": ai_assisted,
        "contains_internal_details": contains_internal_details,
        "reviewer_name": reviewer_name,
        "reviewer_role": reviewer_role,
        "warning_acknowledged": warning_acknowledged,
        "human_confirmation_text": human_confirmation_text,
    }

    required_phrase = "I CONFIRM HUMAN REVIEW"
    if not reviewer_name or len(reviewer_name) < 3:
        return metadata, "Human reviewer name is required for any outbound external communication."
    if not warning_acknowledged:
        return metadata, "Warning acknowledgement is required before any outbound external communication is sent."
    if human_confirmation_text != required_phrase:
        return metadata, f"Human authorization confirmation must match '{required_phrase}'."

    return metadata, None


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

    transition_fields = {
        key: value
        for key, value in payload.items()
        if key not in {"new_status", "approval_request_id"}
    }
    try:
        if payload["new_status"] == "closed":
            approval_request_id = payload.get("approval_request_id")
            if approval_request_id is None:
                return jsonify({"error": "approval_request_id is required for closeout transitions"}), 400
            try:
                approval_request_id = int(approval_request_id)
            except (TypeError, ValueError):
                return jsonify({"error": "approval_request_id must be an integer"}), 400

            grant = _grants().close_grant_with_approval(
                grant_id,
                _org_id(),
                approval_request_id=approval_request_id,
                executed_by_user_id=int(current_user.id),
            )
        else:
            grant = _grants().advance_grant_status(
                grant_id,
                _org_id(),
                new_status=payload["new_status"],
                **transition_fields,
            )
    except GrantNotFound as exc:
        return jsonify({"error": str(exc)}), 404
    except InvalidGrantTransition as exc:
        return jsonify({"error": str(exc)}), 422
    except GrantApprovalError as exc:
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


# ---------------------------------------------------------------------------
# Campaign routes
# ---------------------------------------------------------------------------

@v2_bp.route("/campaigns", methods=["GET"])
@login_required
@roles_required("admin", "staff")
def list_campaigns_route():
    """List campaigns for the current org."""
    from ngo_homesuite.services.campaign_service import list_campaigns
    status = request.args.get("status")
    campaign_type = request.args.get("campaign_type")
    campaigns = list_campaigns(_org_id(), status=status, campaign_type=campaign_type)
    return jsonify([
        {
            "id": c.id,
            "name": c.name,
            "slug": c.slug,
            "description": c.description,
            "campaign_type": c.campaign_type,
            "status": c.status,
            "goal_amount": float(c.goal_amount),
            "raised_amount": float(c.raised_amount),
            "currency": c.currency,
            "photo_url": _campaign_photo_url(c.id, getattr(c, 'photo_path', None)),
            "start_date": str(c.start_date) if c.start_date else None,
            "end_date": str(c.end_date) if c.end_date else None,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in campaigns
    ])


@v2_bp.route("/campaigns", methods=["POST"])
@login_required
@roles_required("admin")
def create_campaign_route():
    """Create a new campaign."""
    from ngo_homesuite.services.campaign_service import create_campaign
    data = _json_or_400(["name"])
    start_date = None
    end_date = None
    if data.get("start_date"):
        try:
            start_date = _parse_iso_date(data["start_date"])
        except ValueError:
            return jsonify({"error": "Invalid start_date format, use YYYY-MM-DD"}), 400
    if data.get("end_date"):
        try:
            end_date = _parse_iso_date(data["end_date"])
        except ValueError:
            return jsonify({"error": "Invalid end_date format, use YYYY-MM-DD"}), 400
    try:
        campaign = create_campaign(
            _org_id(),
            name=data["name"],
            campaign_type=data.get("campaign_type", "general"),
            status=data.get("status", "draft"),
            description=data.get("description"),
            goal_amount=float(data.get("goal_amount", 0)),
            currency=data.get("currency", "USD"),
            start_date=start_date,
            end_date=end_date,
            fund_id=data.get("fund_id"),
            notes=data.get("notes"),
            slug=data.get("slug"),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({
        "id": campaign.id,
        "slug": campaign.slug,
        "photo_url": _campaign_photo_url(campaign.id, getattr(campaign, 'photo_path', None)),
    }), 201


@v2_bp.route("/campaigns/<int:campaign_id>", methods=["GET"])
@login_required
@roles_required("admin", "staff")
def get_campaign_route(campaign_id: int):
    """Get campaign detail + live stats."""
    from ngo_homesuite.services.campaign_service import campaign_stats, get_campaign
    try:
        stats = campaign_stats(campaign_id, _org_id())
    except LookupError:
        return jsonify({"error": "Campaign not found"}), 404
    campaign = get_campaign(campaign_id, _org_id())
    stats["photo_url"] = _campaign_photo_url(campaign_id, getattr(campaign, 'photo_path', None) if campaign else None)
    return jsonify(stats)


@v2_bp.route("/campaigns/<int:campaign_id>", methods=["PATCH"])
@login_required
@roles_required("admin")
def update_campaign_route(campaign_id: int):
    """Update mutable campaign fields."""
    from ngo_homesuite.services.campaign_service import update_campaign
    data = _json_or_400()
    # Convert date strings if provided
    for date_field in ("start_date", "end_date"):
        if data.get(date_field):
            try:
                data[date_field] = _parse_iso_date(data[date_field])
            except ValueError:
                return jsonify({"error": f"Invalid {date_field} format, use YYYY-MM-DD"}), 400
    try:
        campaign = update_campaign(campaign_id, _org_id(), **data)
    except LookupError:
        return jsonify({"error": "Campaign not found"}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({
        "id": campaign.id,
        "status": campaign.status,
        "photo_url": _campaign_photo_url(campaign.id, getattr(campaign, 'photo_path', None)),
    })


@v2_bp.route("/campaigns/<int:campaign_id>/photo", methods=["POST"])
@login_required
@roles_required("admin", "staff")
def upload_campaign_photo_route(campaign_id: int):
    from ngo_homesuite.services.campaign_service import get_campaign

    campaign = get_campaign(campaign_id, _org_id())
    if campaign is None:
        return jsonify({"error": "Campaign not found"}), 404

    uploaded = request.files.get("photo")
    if uploaded is None or not uploaded.filename:
        return jsonify({"error": "photo file is required"}), 400

    try:
        campaign.photo_path = _save_campaign_photo_upload(uploaded, org_id=_org_id(), campaign_id=campaign_id)
        db.session.commit()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify({
        "id": campaign.id,
        "photo_url": _campaign_photo_url(campaign.id, campaign.photo_path),
    })


@v2_bp.route("/campaigns/<int:campaign_id>/close", methods=["POST"])
@login_required
@roles_required("admin")
def close_campaign_route(campaign_id: int):
    """Close a campaign."""
    from ngo_homesuite.services.campaign_service import update_campaign
    try:
        campaign = update_campaign(campaign_id, _org_id(), status="closed")
    except LookupError:
        return jsonify({"error": "Campaign not found"}), 404
    return jsonify({"id": campaign.id, "status": campaign.status})


@v2_bp.route("/campaigns/<int:campaign_id>/emails/send", methods=["POST"])
@login_required
@roles_required("admin", "staff")
def campaign_send_emails_route(campaign_id: int):
    """Send (or preview) a bulk campaign email to a donor audience."""
    from ngo_homesuite.services.campaign_email_service import send_campaign_bulk_email

    actor_role = str(getattr(current_user, "role", "") or "").strip().lower()
    actor_granted = bool(getattr(current_user, "can_authorize_external_comms", False))
    if actor_role != "admin" and not actor_granted:
        return jsonify(
            {
                "error": "User is not authorized for outbound external communications.",
                "required_permission": "can_authorize_external_comms",
            }
        ), 403

    data = _json_or_400(["subject", "body"])
    hitl_metadata, hitl_error = _human_in_the_loop_metadata(data)
    if hitl_error:
        return jsonify({
            "error": hitl_error,
            "warning": "All outbound external communication requires explicit human authorization.",
            "human_in_the_loop_required": True,
        }), 400

    audience_payload = data.get("audience") if isinstance(data.get("audience"), dict) else {}
    audience_payload = dict(audience_payload)
    audience_payload["_human_in_the_loop"] = hitl_metadata

    try:
        payload = send_campaign_bulk_email(
            _org_id(),
            campaign_id,
            created_by_user_id=int(getattr(current_user, "id", 0) or 0),
            created_by_username=str(getattr(current_user, "username", "") or ""),
            created_by_role=str(getattr(current_user, "role", "") or ""),
            subject=str(data.get("subject") or ""),
            body=str(data.get("body") or ""),
            audience=audience_payload,
            human_authorization=hitl_metadata,
            dry_run=bool(data.get("dry_run", False)),
        )
    except LookupError:
        return jsonify({"error": "Campaign not found"}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(payload), 200


@v2_bp.route("/campaigns/<int:campaign_id>/emails/preview", methods=["POST"])
@login_required
@roles_required("admin", "staff")
def campaign_preview_emails_route(campaign_id: int):
    """Preview recipient count, personalization, and quality hints before sending."""
    from ngo_homesuite.services.campaign_email_service import preview_campaign_email

    data = _json_or_400(["subject", "body"])
    try:
        payload = preview_campaign_email(
            _org_id(),
            campaign_id,
            subject=str(data.get("subject") or ""),
            body=str(data.get("body") or ""),
            audience=data.get("audience") if isinstance(data.get("audience"), dict) else {},
        )
    except LookupError:
        return jsonify({"error": "Campaign not found"}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(payload), 200


@v2_bp.route("/campaigns/<int:campaign_id>/emails/ai-draft", methods=["POST"])
@login_required
@roles_required("admin", "staff")
def campaign_ai_draft_route(campaign_id: int):
    """Generate an AI-assisted campaign email draft with fallback when AI is unavailable."""
    from ngo_homesuite.services.campaign_email_service import generate_ai_campaign_email_draft

    data = request.get_json(silent=True) or {}
    try:
        payload = generate_ai_campaign_email_draft(
            _org_id(),
            campaign_id,
            objective=str(data.get("objective") or ""),
            tone=str(data.get("tone") or ""),
            audience=data.get("audience") if isinstance(data.get("audience"), dict) else {},
            ask_amount=float(data.get("ask_amount")) if data.get("ask_amount") is not None else None,
        )
    except LookupError:
        return jsonify({"error": "Campaign not found"}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(payload), 200


@v2_bp.route("/campaigns/<int:campaign_id>/emails/analytics", methods=["GET"])
@login_required
@roles_required("admin", "staff")
def campaign_email_analytics_route(campaign_id: int):
    """Return aggregate analytics for campaign bulk email sends."""
    from ngo_homesuite.services.campaign_email_service import campaign_email_analytics

    try:
        payload = campaign_email_analytics(_org_id(), campaign_id)
    except LookupError:
        return jsonify({"error": "Campaign not found"}), 404
    return jsonify(payload), 200
