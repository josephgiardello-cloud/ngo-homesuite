"""JSON API routes for Grants, Tasks, Program Impact, Smart Groups, and P2P Fundraising.

All routes are prefixed with /api/v2 and require login.
"""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import date, datetime, timezone
from io import BytesIO
from pathlib import Path
import re
import time
from typing import Any
import uuid
from urllib.parse import unquote
import warnings

from flask import Blueprint, Response, current_app, jsonify, redirect, request
from flask_login import current_user, login_required
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from werkzeug.utils import secure_filename

from ngo_homesuite.grants.facade import GrantsFacade
from ngo_homesuite.grants.exceptions import GrantApprovalError, GrantNotFound, InvalidGrantTransition
from ngo_homesuite.models.core import CampaignEmailDelivery, Donor, Grant, User, db

from ngo_homesuite.web.rbac import roles_required

v2_bp = Blueprint("v2", __name__, url_prefix="/api/v2")
_GRANTS_FACADE = GrantsFacade()
_PHOTO_ALLOWED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}
_PHOTO_MAX_BYTES = 5 * 1024 * 1024
_TRACKING_MAX_REQUESTS_PER_WINDOW = 10
_TRACKING_RATE_WINDOW_SECONDS = 60.0
_TRACKING_REQUESTS_BY_IP: dict[str, deque[float]] = defaultdict(deque)
_CAMPAIGN_SEND_MAX_REQUESTS_PER_WINDOW = 8
_CAMPAIGN_SEND_RATE_WINDOW_SECONDS = 60.0
_CAMPAIGN_SEND_REQUESTS_BY_KEY: dict[str, deque[float]] = defaultdict(deque)
_TRACKING_PIXEL = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,"
    b"\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)


def _org_id() -> int:
    return int(current_user.organization_id)


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _json_or_400(required: list[str] | None = None) -> dict[str, Any]:
    data = request.get_json(silent=True) or {}
    if required:
        missing = [k for k in required if k not in data]
        if missing:
            from flask import abort
            abort(400, description=f"Missing required fields: {missing}")
    return data


def _tracking_ip_limited() -> bool:
    ip = str(request.headers.get("X-Forwarded-For") or request.remote_addr or "unknown").split(",", 1)[0].strip()
    now = time.monotonic()
    cutoff = now - _TRACKING_RATE_WINDOW_SECONDS
    bucket = _TRACKING_REQUESTS_BY_IP[ip]
    while bucket and bucket[0] <= cutoff:
        bucket.popleft()
    if len(bucket) >= _TRACKING_MAX_REQUESTS_PER_WINDOW:
        return True
    bucket.append(now)
    return False


def _tracking_request_args() -> tuple[int, int, int, int, str]:
    campaign_id = int(request.args.get("campaign_id", "0") or 0)
    donor_id = int(request.args.get("donor_id", "0") or 0)
    delivery_id = int(request.args.get("delivery_id", "0") or 0)
    issued_at = int(request.args.get("ts", "0") or 0)
    signature = str(request.args.get("sig", "") or "").strip()
    return campaign_id, donor_id, delivery_id, issued_at, signature


def _campaign_send_limited(campaign_id: int) -> tuple[bool, int]:
    actor_id = int(getattr(current_user, "id", 0) or 0)
    org_id = int(getattr(current_user, "organization_id", 0) or 0)
    key = f"{org_id}:{actor_id}:{int(campaign_id)}"
    now = time.monotonic()
    cutoff = now - _CAMPAIGN_SEND_RATE_WINDOW_SECONDS
    bucket = _CAMPAIGN_SEND_REQUESTS_BY_KEY[key]
    while bucket and bucket[0] <= cutoff:
        bucket.popleft()
    if len(bucket) >= _CAMPAIGN_SEND_MAX_REQUESTS_PER_WINDOW:
        retry_after = max(1, int((bucket[0] + _CAMPAIGN_SEND_RATE_WINDOW_SECONDS) - now))
        return True, retry_after
    bucket.append(now)
    return False, 0


def _parse_iso_date(value: str) -> date:
    return date.fromisoformat((value or "").strip())


def _grants():
    return _GRANTS_FACADE


def _search_profile_dict(profile) -> dict[str, Any]:
    return {
        "id": int(profile.id),
        "name": str(profile.name or ""),
        "source": str(profile.source or ""),
        "query": str(profile.query or ""),
        "applicant_profile": str(profile.applicant_profile or ""),
        "requested_amount": float(profile.requested_amount) if profile.requested_amount is not None else None,
        "statuses_csv": str(profile.statuses_csv or ""),
        "alert_channel": str(profile.alert_channel or ""),
        "is_active": bool(profile.is_active),
        "last_checked_at": profile.last_checked_at.isoformat() if profile.last_checked_at else None,
        "last_result_count": int(profile.last_result_count or 0),
    }


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


def _extract_grant_guideline_text(uploaded) -> tuple[str, str]:
    if uploaded is None or not getattr(uploaded, "filename", None):
        raise ValueError("guideline_file is required")

    filename = secure_filename(str(uploaded.filename or "")) or "guideline.txt"
    suffix = Path(filename).suffix.lower()
    payload = uploaded.read()
    uploaded.stream.seek(0)
    if not payload:
        raise ValueError("guideline file is empty")

    if suffix in {".txt", ".md", ".csv"}:
        text = payload.decode("utf-8", errors="ignore")
    elif suffix in {".html", ".htm"}:
        html_text = payload.decode("utf-8", errors="ignore")
        text = re.sub(r"<[^>]+>", " ", html_text)
    elif suffix == ".pdf":
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            from PyPDF2 import PdfReader

        reader = PdfReader(BytesIO(payload))
        pages = [page.extract_text() or "" for page in reader.pages]
        text = "\n".join(pages)
    else:
        raise ValueError("unsupported guideline file type")

    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        raise ValueError("unable to extract text from guideline file")
    return filename, normalized


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


@v2_bp.route("/grants/opportunities/search", methods=["GET"])
@login_required
def grants_search_opportunities():
    q = (request.args.get("q") or "").strip() or None
    applicant_profile = (request.args.get("applicant_profile") or "").strip() or None
    requested_amount_raw = request.args.get("requested_amount")
    deadline_before_raw = request.args.get("deadline_before")
    statuses_raw = request.args.get("statuses")
    limit = request.args.get("limit", 50, type=int)

    requested_amount = None
    if requested_amount_raw not in (None, ""):
        try:
            requested_amount = float(requested_amount_raw)
        except (TypeError, ValueError):
            return jsonify({"error": "requested_amount must be numeric"}), 400

    deadline_before = None
    if deadline_before_raw:
        try:
            deadline_before = _parse_iso_date(str(deadline_before_raw))
        except ValueError:
            return jsonify({"error": "deadline_before must be ISO format YYYY-MM-DD"}), 400

    statuses = None
    if statuses_raw:
        statuses = [part.strip() for part in str(statuses_raw).split(",") if part.strip()]

    try:
        results = _grants().search_applicable_opportunities(
            _org_id(),
            q=q,
            applicant_profile=applicant_profile,
            requested_amount=requested_amount,
            deadline_before=deadline_before,
            statuses=statuses,
            limit=max(1, min(int(limit), 200)),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify({"count": len(results), "results": results})


@v2_bp.route("/grants/external/grants-gov/search", methods=["GET"])
@login_required
def grants_external_grants_gov_search():
    q = (request.args.get("q") or "").strip() or None
    applicant_profile = (request.args.get("applicant_profile") or "").strip() or None
    requested_amount_raw = request.args.get("requested_amount")
    limit = request.args.get("limit", 25, type=int)
    sync = str(request.args.get("sync") or "false").strip().lower() in {"1", "true", "yes"}

    requested_amount = None
    if requested_amount_raw not in (None, ""):
        try:
            requested_amount = float(requested_amount_raw)
        except (TypeError, ValueError):
            return jsonify({"error": "requested_amount must be numeric"}), 400

    try:
        results = _grants().search_grants_gov_opportunities(
            _org_id(),
            q=q,
            applicant_profile=applicant_profile,
            requested_amount=requested_amount,
            limit=max(1, min(int(limit), 100)),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    synced_count = 0
    if sync and results:
        synced = _grants().sync_grants_gov_results(_org_id(), results)
        synced_by_external = {str(item.external_opportunity_id or ""): item for item in synced}
        for item in results:
            local = synced_by_external.get(str(item.get("external_opportunity_id") or ""))
            if local is not None:
                item["opportunity_id"] = int(local.id)
        synced_count = len(synced)

    return jsonify({"count": len(results), "synced_count": synced_count, "results": results})


@v2_bp.route("/grants/opportunities/<int:opportunity_id>/ai-context", methods=["GET"])
@login_required
def grants_opportunity_ai_context(opportunity_id: int):
    try:
        payload = _grants().get_opportunity_ai_context(opportunity_id, _org_id())
    except LookupError:
        return jsonify({"error": "opportunity not found"}), 404
    return jsonify(payload)


@v2_bp.route("/grants/search-profiles", methods=["GET"])
@login_required
def grants_search_profiles_list():
    active_only = str(request.args.get("active_only") or "false").strip().lower() in {"1", "true", "yes"}
    profiles = _grants().list_search_profiles(_org_id(), active_only=active_only)
    return jsonify({"count": len(profiles), "results": [_search_profile_dict(profile) for profile in profiles]})


@v2_bp.route("/grants/search-profiles", methods=["POST"])
@login_required
@roles_required("admin", "staff")
def grants_search_profiles_create():
    data = request.get_json(silent=True) or {}
    requested_amount = data.get("requested_amount")
    if requested_amount not in (None, ""):
        try:
            requested_amount = float(requested_amount)
        except (TypeError, ValueError):
            return jsonify({"error": "requested_amount must be numeric"}), 400
    else:
        requested_amount = None

    try:
        profile = _grants().create_search_profile(
            _org_id(),
            name=str(data.get("name") or "").strip(),
            source=str(data.get("source") or "grants_gov").strip() or "grants_gov",
            query=str(data.get("query") or "").strip() or None,
            applicant_profile=str(data.get("applicant_profile") or "").strip() or None,
            requested_amount=requested_amount,
            statuses_csv=str(data.get("statuses_csv") or "").strip() or None,
            alert_channel=str(data.get("alert_channel") or "in_app").strip() or "in_app",
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify(_search_profile_dict(profile)), 201


@v2_bp.route("/grants/search-profiles/<int:profile_id>/run", methods=["POST"])
@login_required
@roles_required("admin", "staff")
def grants_search_profile_run(profile_id: int):
    try:
        result = _grants().run_search_profile(profile_id, _org_id())
    except LookupError:
        return jsonify({"error": "search profile not found"}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)


@v2_bp.route("/grants/search-alerts", methods=["GET"])
@login_required
def grants_search_alerts_list():
    status = (request.args.get("status") or "").strip() or None
    limit = request.args.get("limit", 50, type=int)
    alerts = _grants().list_search_alerts(_org_id(), status=status, limit=max(1, min(int(limit), 200)))
    return jsonify({"count": len(alerts), "results": alerts})


@v2_bp.route("/grants/search-alerts/<int:alert_id>/acknowledge", methods=["POST"])
@login_required
@roles_required("admin", "staff")
def grants_search_alert_acknowledge(alert_id: int):
    data = request.get_json(silent=True) or {}
    new_status = str(data.get("status") or "reviewed").strip() or "reviewed"
    notes = str(data.get("notes") or "").strip() or None
    try:
        result = _grants().acknowledge_search_alert(
            alert_id,
            _org_id(),
            new_status=new_status,
            notes=notes,
        )
    except LookupError:
        return jsonify({"error": "alert not found"}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)


@v2_bp.route("/grants/opportunities/<int:opportunity_id>/compliance-guidance", methods=["POST"])
@login_required
@roles_required("admin", "staff", "viewer")
def grants_opportunity_compliance_guidance(opportunity_id: int):
    data = request.get_json(silent=True) or {}
    proposal_text = str(data.get("proposal_text") or "").strip() or None

    try:
        guidance = _grants().generate_proposal_compliance_guidance(
            opportunity_id,
            _org_id(),
            proposal_text=proposal_text,
        )
    except LookupError:
        return jsonify({"error": "opportunity not found"}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify(guidance)


@v2_bp.route("/grants/opportunities/<int:opportunity_id>/draft-assist", methods=["POST"])
@login_required
@roles_required("admin", "staff", "viewer")
def grants_opportunity_draft_assist(opportunity_id: int):
    data = request.get_json(silent=True) or {}
    amount_requested = data.get("amount_requested")
    if amount_requested not in (None, ""):
        try:
            amount_requested = float(amount_requested)
        except (TypeError, ValueError):
            return jsonify({"error": "amount_requested must be numeric"}), 400
    else:
        amount_requested = None

    try:
        draft = _grants().generate_proposal_draft_assist(
            opportunity_id,
            _org_id(),
            organization_summary=str(data.get("organization_summary") or "").strip() or None,
            program_summary=str(data.get("program_summary") or "").strip() or None,
            applicant_profile=str(data.get("applicant_profile") or "").strip() or None,
            amount_requested=amount_requested,
            existing_draft=str(data.get("existing_draft") or "").strip() or None,
        )
    except LookupError:
        return jsonify({"error": "opportunity not found"}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify(draft)


@v2_bp.route("/grants/opportunities/<int:opportunity_id>/guidelines/ingest", methods=["POST"])
@login_required
@roles_required("admin", "staff", "viewer")
def grants_opportunity_guideline_ingest(opportunity_id: int):
    source_name = None
    guideline_text = None
    merge_into_notes = True

    if request.content_type and request.content_type.startswith("multipart/form-data"):
        uploaded = request.files.get("guideline_file")
        try:
            source_name, guideline_text = _extract_grant_guideline_text(uploaded)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        merge_into_notes = str(request.form.get("merge_into_notes") or "true").strip().lower() not in {"0", "false", "no"}
    else:
        data = request.get_json(silent=True) or {}
        source_name = str(data.get("source_name") or "manual").strip() or "manual"
        guideline_text = str(data.get("guideline_text") or "").strip() or None
        merge_into_notes = bool(data.get("merge_into_notes", True))

    try:
        result = _grants().ingest_opportunity_guidance(
            opportunity_id,
            _org_id(),
            guideline_text=str(guideline_text or ""),
            source_name=source_name,
            merge_into_notes=merge_into_notes,
        )
    except LookupError:
        return jsonify({"error": "opportunity not found"}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify(result)


@v2_bp.route("/grants/opportunities/<int:opportunity_id>/draft-assist/save", methods=["POST"])
@login_required
@roles_required("admin", "staff", "viewer")
def grants_opportunity_draft_assist_save(opportunity_id: int):
    data = request.get_json(silent=True) or {}
    amount_requested = data.get("amount_requested")
    if amount_requested not in (None, ""):
        try:
            amount_requested = float(amount_requested)
        except (TypeError, ValueError):
            return jsonify({"error": "amount_requested must be numeric"}), 400
    else:
        amount_requested = None

    try:
        proposal = _grants().save_draft_assist_as_proposal(
            opportunity_id,
            _org_id(),
            organization_summary=str(data.get("organization_summary") or "").strip() or None,
            program_summary=str(data.get("program_summary") or "").strip() or None,
            applicant_profile=str(data.get("applicant_profile") or "").strip() or None,
            amount_requested=amount_requested,
            existing_draft=str(data.get("existing_draft") or "").strip() or None,
            document_ref=str(data.get("document_ref") or "").strip() or None,
        )
    except LookupError:
        return jsonify({"error": "opportunity not found"}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify(
        {
            "proposal_id": int(proposal.id),
            "opportunity_id": int(proposal.opportunity_id),
            "version_number": int(proposal.version_number),
            "amount_requested": float(proposal.amount_requested) if proposal.amount_requested is not None else None,
            "document_ref": proposal.document_ref,
            "narrative_summary": proposal.narrative_summary,
            "notes": proposal.notes,
        }
    ), 201


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

    limited, retry_after = _campaign_send_limited(campaign_id)
    if limited:
        response = jsonify({
            "error": "Rate limit exceeded for campaign email send. Please retry shortly.",
            "retry_after_sec": retry_after,
        })
        response.status_code = 429
        response.headers["Retry-After"] = str(retry_after)
        return response

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
        scheduled_at_raw = data.get("scheduled_at")
        scheduled_at_dt = None
        if scheduled_at_raw:
            from datetime import datetime as _dt
            try:
                scheduled_at_dt = _dt.fromisoformat(str(scheduled_at_raw).replace("Z", "+00:00")).replace(tzinfo=None)
            except (ValueError, TypeError):
                scheduled_at_dt = None

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
            scheduled_at=scheduled_at_dt,
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


@v2_bp.get("/campaigns/email/segments")
@login_required
@roles_required("admin", "staff")
def campaign_email_segments_route():
    """List saved Smart Groups usable as campaign email segments."""
    from ngo_homesuite.services.smart_groups_service import list_groups

    groups = list_groups(_org_id())
    items = [
        {
            "id": int(group.id),
            "name": str(group.name or ""),
            "description": str(group.description or ""),
            "last_count": int(group.last_count or 0),
            "last_evaluated_at": group.last_evaluated_at.isoformat() if group.last_evaluated_at else None,
        }
        for group in groups
    ]
    return jsonify(items), 200


@v2_bp.post("/campaigns/email/segments")
@login_required
@roles_required("admin", "staff")
def campaign_email_segments_create_route():
    """Quick-create a saved campaign email segment using Smart Groups rules."""
    from ngo_homesuite.services.smart_groups_service import create_group, evaluate_group

    data = _json_or_400(required=["name", "rules"])
    include_preview = bool(data.get("include_preview", False))

    preview_limit_raw = data.get("preview_limit", 25)
    try:
        preview_limit = int(preview_limit_raw)
    except (TypeError, ValueError):
        return jsonify({"error": "preview_limit must be an integer"}), 400
    preview_limit = max(1, min(preview_limit, 500))

    try:
        group = create_group(
            _org_id(),
            name=str(data.get("name") or "").strip(),
            rules=data.get("rules"),
            description=str(data.get("description") or "").strip() or None,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "A segment with this name already exists."}), 409

    payload = {
        "id": int(group.id),
        "name": str(group.name or ""),
        "description": str(group.description or ""),
        "last_count": int(group.last_count or 0),
        "last_evaluated_at": group.last_evaluated_at.isoformat() if group.last_evaluated_at else None,
    }

    if include_preview:
        members = evaluate_group(int(group.id), _org_id())
        payload["count"] = int(len(members))
        payload["members"] = members[:preview_limit]

    return jsonify(payload), 201


@v2_bp.get("/campaigns/email/segments/<int:segment_id>/preview")
@login_required
@roles_required("admin", "staff")
def campaign_email_segment_preview_route(segment_id: int):
    """Evaluate and return a member preview for a saved campaign email segment."""
    from werkzeug.exceptions import NotFound

    from ngo_homesuite.services.smart_groups_service import evaluate_group

    limit_raw = request.args.get("limit", "200")
    try:
        limit = int(limit_raw)
    except (TypeError, ValueError):
        return jsonify({"error": "limit must be an integer"}), 400

    limit = max(1, min(limit, 500))
    try:
        members = evaluate_group(int(segment_id), _org_id())
    except NotFound:
        return jsonify({"error": "Segment not found"}), 404

    return jsonify({
        "segment_id": int(segment_id),
        "count": int(len(members)),
        "members": members[:limit],
    }), 200


@v2_bp.get("/campaigns/email/open-pixel")
def campaign_email_open_pixel() -> Response:
    """Record an email open and return a 1x1 GIF pixel."""
    if _tracking_ip_limited():
        return Response("rate limit exceeded", status=429)

    from ngo_homesuite.services.campaign_email_service import verify_tracking_signature

    campaign_id, donor_id, delivery_id, issued_at, signature = _tracking_request_args()
    max_age_seconds = int(current_app.config.get("TRACKING_URL_MAX_AGE_SECONDS", 604800) or 604800)
    if campaign_id > 0 and donor_id > 0 and delivery_id > 0 and signature:
        valid = verify_tracking_signature(
            kind="open",
            campaign_id=campaign_id,
            donor_id=donor_id,
            delivery_id=delivery_id,
            issued_at=issued_at,
            signature=signature,
            max_age_seconds=max_age_seconds,
        )
        if valid:
            delivery = db.session.get(CampaignEmailDelivery, int(delivery_id))
            if (
                delivery is not None
                and int(delivery.campaign_id) == int(campaign_id)
                and int(delivery.donor_id or 0) == int(donor_id)
            ):
                delivery.open_count = int(delivery.open_count or 0) + 1
                delivery.last_opened_at = _utcnow_naive()
                db.session.commit()

    resp = Response(_TRACKING_PIXEL, mimetype="image/gif")
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp


@v2_bp.get("/campaigns/email/click")
def campaign_email_click_redirect():
    """Record a tracked click and redirect to the original target URL."""
    if _tracking_ip_limited():
        return jsonify({"error": "rate limit exceeded"}), 429

    from ngo_homesuite.services.campaign_email_service import verify_tracking_signature

    campaign_id, donor_id, delivery_id, issued_at, signature = _tracking_request_args()
    target_url = unquote(request.args.get("url", "").strip())
    if not (target_url.startswith("http://") or target_url.startswith("https://")):
        target_url = "/"

    max_age_seconds = int(current_app.config.get("TRACKING_URL_MAX_AGE_SECONDS", 604800) or 604800)
    valid = verify_tracking_signature(
        kind="click",
        campaign_id=campaign_id,
        donor_id=donor_id,
        delivery_id=delivery_id,
        issued_at=issued_at,
        signature=signature,
        target_url=target_url,
        max_age_seconds=max_age_seconds,
    )
    if not valid:
        return jsonify({"error": "invalid or expired tracking token"}), 400

    delivery = db.session.get(CampaignEmailDelivery, int(delivery_id))
    if (
        delivery is None
        or int(delivery.campaign_id) != int(campaign_id)
        or int(delivery.donor_id or 0) != int(donor_id)
    ):
        return jsonify({"error": "tracking record not found"}), 404
    delivery.click_count = int(delivery.click_count or 0) + 1
    delivery.last_clicked_at = _utcnow_naive()
    db.session.commit()

    return redirect(target_url, code=302)


@v2_bp.get("/campaigns/email/unsubscribe")
def campaign_email_unsubscribe():
    """Process an unsubscribe request via a signed link from a campaign email."""
    from ngo_homesuite.services.campaign_email_service import verify_unsub_signature
    from ngo_homesuite.models.core import CampaignEmailOptOut

    email = request.args.get("email", "").strip().lower()
    donor_id_raw = request.args.get("donor_id", "0")
    campaign_id_raw = request.args.get("campaign_id", "0")
    issued_at_raw = request.args.get("ts", "0")
    signature = request.args.get("sig", "")

    try:
        donor_id = int(donor_id_raw)
        campaign_id = int(campaign_id_raw)
        issued_at = int(issued_at_raw)
    except (ValueError, TypeError):
        return "<html><body><h2>Invalid unsubscribe link.</h2></body></html>", 400

    if not email or not verify_unsub_signature(
        email=email,
        donor_id=donor_id,
        campaign_id=campaign_id,
        issued_at=issued_at,
        signature=signature,
    ):
        return "<html><body><h2>Invalid or expired unsubscribe link.</h2></body></html>", 400

    # Find organization via campaign
    from ngo_homesuite.models.core import Campaign as _Campaign
    campaign_obj = db.session.get(_Campaign, campaign_id)
    org_id = int(campaign_obj.organization_id) if campaign_obj else 0
    if not org_id:
        return "<html><body><h2>Campaign not found.</h2></body></html>", 404

    # Idempotent: only insert if not already opted out
    existing = db.session.scalars(
        select(CampaignEmailOptOut).where(
            CampaignEmailOptOut.organization_id == org_id,
            CampaignEmailOptOut.email == email,
        ).limit(1)
    ).first()
    if not existing:
        token = signature[:64]
        opt_out = CampaignEmailOptOut(
            organization_id=org_id,
            donor_id=donor_id if donor_id > 0 else None,
            email=email,
            token=token,
            campaign_id=campaign_id if campaign_id > 0 else None,
        )
        db.session.add(opt_out)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()

    return (
        "<html><head><title>Unsubscribed</title>"
        "<style>body{font-family:Arial,sans-serif;max-width:480px;margin:80px auto;text-align:center;}</style></head>"
        "<body><h2>You have been unsubscribed.</h2>"
        "<p>You will no longer receive campaign emails from this organization.</p>"
        "</body></html>"
    ), 200


# ---------------------------------------------------------------------------
# Campaign Projection (E-1)
# ---------------------------------------------------------------------------


@v2_bp.get("/campaigns/<int:campaign_id>/projection")
@login_required
@roles_required('admin', 'staff')
def campaign_projection(campaign_id: int):
    """Return a fundraising trajectory projection for a campaign."""
    from ngo_homesuite.services.campaign_projection_service import (
        project_campaign,
        project_with_conversion_boost,
    )

    org_id = _org_id()
    boost_raw = request.args.get('boost_pct')

    try:
        if boost_raw is not None:
            boost = float(boost_raw)
            if boost <= -100.0:
                return jsonify({'error': 'boost_pct must be greater than -100'}), 400
            result = project_with_conversion_boost(campaign_id, org_id, boost)
        else:
            result = project_campaign(campaign_id, org_id)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 404

    return jsonify(result), 200
