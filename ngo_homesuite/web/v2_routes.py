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

from flask import Blueprint, Response, current_app, g, jsonify, redirect, request
from flask_login import current_user, login_required
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from werkzeug.utils import secure_filename

from ngo_homesuite.grants.facade import GrantsFacade
from ngo_homesuite.grants.exceptions import GrantApprovalError, GrantNotFound, InvalidGrantTransition
from ngo_homesuite.models.core import CampaignEmailDelivery, Donation, Donor, DonorSoftCredit, Grant, Organization, User, db

from ngo_homesuite.web.auth_routes import require_step_up_auth
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


def _form_ingest_token() -> str:
    return str(current_app.config.get("FORM_ECOSYSTEM_INGEST_TOKEN") or "").strip()


def _request_ingest_token() -> str:
    token = str(request.headers.get("X-Form-Ingest-Token") or "").strip()
    if token:
        return token
    auth = str(request.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


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


def _bool_or_none(value: Any, *, field_name: str) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    lowered = str(value).strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{field_name} must be a boolean")


def _normalize_email(value: Any) -> str:
    return str(value or "").strip().lower()


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _normalize_phone(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _dedupe_confidence(reason: str, size: int) -> float:
    if reason == "matching_email":
        return min(0.99, 0.85 + (0.03 * max(0, int(size) - 2)))
    if reason == "matching_name_phone":
        return min(0.95, 0.72 + (0.03 * max(0, int(size) - 2)))
    return 0.6


def _parse_user_ids_csv(raw: str) -> list[int]:
    values: list[int] = []
    for token in str(raw or "").split(","):
        stripped = token.strip()
        if not stripped:
            continue
        try:
            values.append(int(stripped))
        except (TypeError, ValueError):
            continue
    return values


def _collab_message_dict(message) -> dict[str, Any]:
    return {
        "id": int(message.id),
        "organization_id": int(message.organization_id),
        "channel_id": int(message.channel_id),
        "sender_user_id": int(message.sender_user_id),
        "body": str(message.body or ""),
        "created_at": message.created_at.isoformat() if message.created_at else None,
        "edited_at": message.edited_at.isoformat() if message.edited_at else None,
    }


@v2_bp.get("/collab/channels")
@login_required
@roles_required("admin", "staff", "viewer")
def list_collaboration_channels():
    from ngo_homesuite.models.core import CollaborationChannel, CollaborationChannelMember, CollaborationMessage

    org_id = _org_id()
    current_user_id = int(getattr(current_user, "id", 0) or 0)
    memberships = list(
        db.session.scalars(
            select(CollaborationChannelMember)
            .where(
                CollaborationChannelMember.organization_id == org_id,
                CollaborationChannelMember.user_id == current_user_id,
                CollaborationChannelMember.is_active.is_(True),
            )
            .order_by(CollaborationChannelMember.joined_at.desc())
        )
    )
    channel_ids = [int(item.channel_id) for item in memberships]
    if not channel_ids:
        return jsonify({"count": 0, "channels": []})

    channels = list(
        db.session.scalars(
            select(CollaborationChannel)
            .where(
                CollaborationChannel.organization_id == org_id,
                CollaborationChannel.id.in_(channel_ids),
                CollaborationChannel.is_archived.is_(False),
            )
            .order_by(CollaborationChannel.updated_at.desc(), CollaborationChannel.id.desc())
        )
    )

    member_rows = list(
        db.session.scalars(
            select(CollaborationChannelMember)
            .where(
                CollaborationChannelMember.organization_id == org_id,
                CollaborationChannelMember.channel_id.in_(channel_ids),
                CollaborationChannelMember.is_active.is_(True),
            )
            .order_by(CollaborationChannelMember.channel_id.asc(), CollaborationChannelMember.user_id.asc())
        )
    )
    members_by_channel: dict[int, list[int]] = defaultdict(list)
    for item in member_rows:
        members_by_channel[int(item.channel_id)].append(int(item.user_id))

    last_read_by_channel = {
        int(item.channel_id): item.last_read_at
        for item in memberships
    }

    latest_messages = list(
        db.session.scalars(
            select(CollaborationMessage)
            .where(
                CollaborationMessage.organization_id == org_id,
                CollaborationMessage.channel_id.in_(channel_ids),
            )
            .order_by(CollaborationMessage.channel_id.asc(), CollaborationMessage.created_at.desc(), CollaborationMessage.id.desc())
        )
    )
    latest_by_channel: dict[int, Any] = {}
    for message in latest_messages:
        latest_by_channel.setdefault(int(message.channel_id), message)

    unread_by_channel: dict[int, int] = {}
    for channel_id in channel_ids:
        last_read_at = last_read_by_channel.get(int(channel_id))
        query = select(func.count(CollaborationMessage.id)).where(
            CollaborationMessage.organization_id == org_id,
            CollaborationMessage.channel_id == int(channel_id),
            CollaborationMessage.sender_user_id != current_user_id,
        )
        if last_read_at is not None:
            query = query.where(CollaborationMessage.created_at > last_read_at)
        unread_by_channel[int(channel_id)] = int(db.session.scalar(query) or 0)

    payload = []
    for channel in channels:
        channel_id = int(channel.id)
        latest = latest_by_channel.get(channel_id)
        payload.append(
            {
                "id": channel_id,
                "organization_id": int(channel.organization_id),
                "channel_type": str(channel.channel_type or "team"),
                "name": channel.name,
                "is_archived": bool(channel.is_archived),
                "member_user_ids": members_by_channel.get(channel_id, []),
                "unread_count": int(unread_by_channel.get(channel_id, 0)),
                "latest_message": _collab_message_dict(latest) if latest is not None else None,
                "updated_at": channel.updated_at.isoformat() if channel.updated_at else None,
            }
        )

    return jsonify({"count": len(payload), "channels": payload})


@v2_bp.post("/collab/channels")
@login_required
@roles_required("admin", "staff")
def create_collaboration_channel():
    from ngo_homesuite.models.core import CollaborationChannel, CollaborationChannelMember

    org_id = _org_id()
    current_user_id = int(getattr(current_user, "id", 0) or 0)
    data = _json_or_400(required=["channel_type"])

    channel_type = str(data.get("channel_type") or "").strip().lower()
    if channel_type not in {"team", "direct"}:
        return jsonify({"error": "channel_type must be one of: team, direct"}), 400

    channel_name = (str(data.get("name") or "").strip() or None)

    member_user_ids: list[int] = []
    if channel_type == "direct":
        participant_raw = data.get("participant_user_id")
        try:
            participant_user_id = int(participant_raw)
        except (TypeError, ValueError):
            return jsonify({"error": "participant_user_id must be an integer"}), 400
        if participant_user_id == current_user_id:
            return jsonify({"error": "direct channel requires another participant"}), 400
        user_ids_sorted = sorted([current_user_id, participant_user_id])

        direct_channel_candidates_stmt = (
            select(
                CollaborationChannelMember.channel_id,
            )
            .where(
                CollaborationChannelMember.organization_id == org_id,
                CollaborationChannelMember.user_id.in_(user_ids_sorted),
                CollaborationChannelMember.is_active.is_(True),
            )
            .group_by(CollaborationChannelMember.channel_id)
            .having(func.count(func.distinct(CollaborationChannelMember.user_id)) == 2)
        )
        candidate_channel_ids = [
            int(row[0])
            for row in db.session.execute(direct_channel_candidates_stmt).all()
        ]
        if candidate_channel_ids:
            candidates = list(
                db.session.scalars(
                    select(CollaborationChannel)
                    .where(
                        CollaborationChannel.organization_id == org_id,
                        CollaborationChannel.channel_type == "direct",
                        CollaborationChannel.id.in_(candidate_channel_ids),
                        CollaborationChannel.is_archived.is_(False),
                    )
                )
            )
            for candidate in candidates:
                member_count = int(
                    db.session.scalar(
                        select(func.count(CollaborationChannelMember.id)).where(
                            CollaborationChannelMember.organization_id == org_id,
                            CollaborationChannelMember.channel_id == int(candidate.id),
                            CollaborationChannelMember.is_active.is_(True),
                        )
                    )
                    or 0
                )
                if member_count == 2:
                    return jsonify(
                        {
                            "created": False,
                            "channel": {
                                "id": int(candidate.id),
                                "channel_type": "direct",
                                "member_user_ids": user_ids_sorted,
                                "name": candidate.name,
                            },
                        }
                    ), 200

        member_user_ids = user_ids_sorted
    else:
        provided_member_ids = data.get("member_user_ids") or []
        parsed_members: list[int] = []
        for item in provided_member_ids:
            try:
                parsed_members.append(int(item))
            except (TypeError, ValueError):
                return jsonify({"error": "member_user_ids must contain integers"}), 400
        member_user_ids = sorted(set(parsed_members + [current_user_id]))
        if channel_name is None:
            return jsonify({"error": "name is required for team channels"}), 400

    if member_user_ids:
        valid_user_ids = {
            int(user_id)
            for user_id in db.session.scalars(
                select(User.id).where(
                    User.organization_id == org_id,
                    User.id.in_(member_user_ids),
                    User.is_active.is_(True),
                )
            )
        }
        if valid_user_ids != set(member_user_ids):
            return jsonify({"error": "one or more members are invalid for this organization"}), 400

    channel = CollaborationChannel(
        organization_id=org_id,
        channel_type=channel_type,
        name=channel_name,
        created_by_user_id=current_user_id,
    )
    db.session.add(channel)
    db.session.flush()

    for user_id in member_user_ids:
        db.session.add(
            CollaborationChannelMember(
                organization_id=org_id,
                channel_id=int(channel.id),
                user_id=int(user_id),
                role=("owner" if int(user_id) == current_user_id else "member"),
            )
        )

    db.session.commit()
    return jsonify(
        {
            "created": True,
            "channel": {
                "id": int(channel.id),
                "organization_id": int(channel.organization_id),
                "channel_type": str(channel.channel_type),
                "name": channel.name,
                "member_user_ids": member_user_ids,
                "created_at": channel.created_at.isoformat() if channel.created_at else None,
            },
        }
    ), 201


@v2_bp.get("/collab/channels/<int:channel_id>/messages")
@login_required
@roles_required("admin", "staff", "viewer")
def list_collaboration_messages(channel_id: int):
    from ngo_homesuite.models.core import CollaborationChannelMember, CollaborationMessage

    org_id = _org_id()
    current_user_id = int(getattr(current_user, "id", 0) or 0)
    membership = db.session.scalar(
        select(CollaborationChannelMember).where(
            CollaborationChannelMember.organization_id == org_id,
            CollaborationChannelMember.channel_id == int(channel_id),
            CollaborationChannelMember.user_id == current_user_id,
            CollaborationChannelMember.is_active.is_(True),
        ).limit(1)
    )
    if membership is None:
        return jsonify({"error": "channel not found"}), 404

    limit = max(1, min(200, int(request.args.get("limit", 100) or 100)))
    before_id = request.args.get("before_id", type=int)

    query = select(CollaborationMessage).where(
        CollaborationMessage.organization_id == org_id,
        CollaborationMessage.channel_id == int(channel_id),
    )
    if before_id is not None:
        query = query.where(CollaborationMessage.id < int(before_id))

    rows = list(
        db.session.scalars(
            query.order_by(CollaborationMessage.id.desc()).limit(limit)
        )
    )
    rows.reverse()

    membership.last_read_at = _utcnow_naive()
    db.session.commit()
    return jsonify({"count": len(rows), "messages": [_collab_message_dict(row) for row in rows]})


@v2_bp.post("/collab/channels/<int:channel_id>/messages")
@login_required
@roles_required("admin", "staff", "viewer")
def create_collaboration_message(channel_id: int):
    from ngo_homesuite.models.core import CollaborationChannelMember, CollaborationMessage

    org_id = _org_id()
    current_user_id = int(getattr(current_user, "id", 0) or 0)
    membership = db.session.scalar(
        select(CollaborationChannelMember).where(
            CollaborationChannelMember.organization_id == org_id,
            CollaborationChannelMember.channel_id == int(channel_id),
            CollaborationChannelMember.user_id == current_user_id,
            CollaborationChannelMember.is_active.is_(True),
        ).limit(1)
    )
    if membership is None:
        return jsonify({"error": "channel not found"}), 404

    data = _json_or_400(required=["body"])
    body = str(data.get("body") or "").strip()
    if not body:
        return jsonify({"error": "body is required"}), 400
    if len(body) > 4000:
        return jsonify({"error": "body must be <= 4000 characters"}), 400

    message = CollaborationMessage(
        organization_id=org_id,
        channel_id=int(channel_id),
        sender_user_id=current_user_id,
        body=body,
    )
    db.session.add(message)
    membership.last_read_at = _utcnow_naive()
    db.session.commit()
    return jsonify(_collab_message_dict(message)), 201


@v2_bp.post("/collab/presence")
@login_required
@roles_required("admin", "staff", "viewer")
def upsert_collaboration_presence():
    from ngo_homesuite.models.core import CollaborationPresence

    org_id = _org_id()
    current_user_id = int(getattr(current_user, "id", 0) or 0)
    data = _json_or_400(required=["status"])

    status = str(data.get("status") or "").strip().lower()
    if status not in {"online", "away", "dnd", "offline"}:
        return jsonify({"error": "status must be one of: online, away, dnd, offline"}), 400

    status_message = (str(data.get("status_message") or "").strip() or None)
    if status_message and len(status_message) > 300:
        return jsonify({"error": "status_message must be <= 300 characters"}), 400

    row = db.session.scalar(
        select(CollaborationPresence).where(
            CollaborationPresence.organization_id == org_id,
            CollaborationPresence.user_id == current_user_id,
        ).limit(1)
    )
    if row is None:
        row = CollaborationPresence(
            organization_id=org_id,
            user_id=current_user_id,
            status=status,
            status_message=status_message,
            last_seen_at=_utcnow_naive(),
        )
        db.session.add(row)
    else:
        row.status = status
        row.status_message = status_message
        row.last_seen_at = _utcnow_naive()

    db.session.commit()
    return jsonify(
        {
            "organization_id": int(row.organization_id),
            "user_id": int(row.user_id),
            "status": str(row.status),
            "status_message": row.status_message,
            "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
    ), 200


@v2_bp.get("/collab/presence")
@login_required
@roles_required("admin", "staff", "viewer")
def list_collaboration_presence():
    from ngo_homesuite.models.core import CollaborationPresence

    org_id = _org_id()
    limit = max(1, min(500, int(request.args.get("limit", 100) or 100)))
    user_ids_filter = _parse_user_ids_csv(str(request.args.get("user_ids") or ""))

    users_query = select(User).where(User.organization_id == org_id, User.is_active.is_(True))
    if user_ids_filter:
        users_query = users_query.where(User.id.in_(user_ids_filter))
    users = list(db.session.scalars(users_query.order_by(User.id.asc()).limit(limit)))
    if not users:
        return jsonify({"count": 0, "items": []})

    user_ids = [int(user.id) for user in users]
    presence_rows = list(
        db.session.scalars(
            select(CollaborationPresence)
            .where(
                CollaborationPresence.organization_id == org_id,
                CollaborationPresence.user_id.in_(user_ids),
            )
            .order_by(CollaborationPresence.user_id.asc())
        )
    )
    by_user_id = {int(row.user_id): row for row in presence_rows}

    payload = []
    for user in users:
        row = by_user_id.get(int(user.id))
        display_name = ((str(user.first_name or "").strip() + " " + str(user.last_name or "").strip()).strip() or str(user.username))
        payload.append(
            {
                "user_id": int(user.id),
                "username": str(user.username),
                "display_name": display_name,
                "status": str(row.status) if row is not None else "offline",
                "status_message": row.status_message if row is not None else None,
                "last_seen_at": row.last_seen_at.isoformat() if row is not None and row.last_seen_at else None,
                "updated_at": row.updated_at.isoformat() if row is not None and row.updated_at else None,
            }
        )

    return jsonify({"count": len(payload), "items": payload})


@v2_bp.get("/dedupe/workbench")
@login_required
@roles_required("admin", "staff")
def dedupe_workbench_route():
    """Return cross-entity duplicate candidates and donor merge opportunities."""
    from ngo_homesuite.models.core import Beneficiary, Volunteer

    entity_scope = str(request.args.get("entity_scope") or "all").strip().lower()
    if entity_scope not in {"all", "donor", "beneficiary", "volunteer"}:
        return jsonify({"error": "entity_scope must be one of all, donor, beneficiary, volunteer"}), 400

    limit_raw = request.args.get("limit", "100")
    max_scan_raw = request.args.get("max_scan", "2000")
    try:
        limit = max(1, min(500, int(limit_raw)))
        max_scan = max(100, min(20000, int(max_scan_raw)))
    except (TypeError, ValueError):
        return jsonify({"error": "limit and max_scan must be integers"}), 400

    org_id = _org_id()
    include_donor = entity_scope in {"all", "donor"}
    include_beneficiary = entity_scope in {"all", "beneficiary"}
    include_volunteer = entity_scope in {"all", "volunteer"}

    records: list[dict[str, Any]] = []

    donor_gift_counts: dict[int, int] = {}
    if include_donor:
        donor_rows = list(
            db.session.scalars(
                select(Donor)
                .where(Donor.organization_id == org_id)
                .order_by(Donor.id.asc())
                .limit(max_scan)
            )
        )
        donor_ids = [int(row.id) for row in donor_rows]
        if donor_ids:
            donor_gift_counts_stmt = (
                select(Donation.donor_id, func.count(Donation.id))
                .where(
                    Donation.organization_id == org_id,
                    Donation.donor_id.in_(donor_ids),
                )
                .group_by(Donation.donor_id)
            )
            gift_rows = db.session.execute(donor_gift_counts_stmt).all()
            donor_gift_counts = {int(donor_id): int(count or 0) for donor_id, count in gift_rows}

        for donor in donor_rows:
            records.append(
                {
                    "entity_type": "donor",
                    "entity_id": int(donor.id),
                    "name": str(donor.name or "").strip(),
                    "email": _normalize_email(donor.email),
                    "phone": _normalize_phone(donor.phone),
                    "raw_phone": str(donor.phone or "").strip(),
                    "donation_count": int(donor_gift_counts.get(int(donor.id), 0)),
                }
            )

    if include_beneficiary:
        beneficiary_rows = list(
            db.session.scalars(
                select(Beneficiary)
                .where(Beneficiary.organization_id == org_id)
                .order_by(Beneficiary.id.asc())
                .limit(max_scan)
            )
        )
        for beneficiary in beneficiary_rows:
            full_name = f"{str(beneficiary.first_name or '').strip()} {str(beneficiary.last_name or '').strip()}".strip()
            records.append(
                {
                    "entity_type": "beneficiary",
                    "entity_id": int(beneficiary.id),
                    "name": full_name,
                    "email": _normalize_email(beneficiary.email),
                    "phone": _normalize_phone(beneficiary.phone),
                    "raw_phone": str(beneficiary.phone or "").strip(),
                    "donation_count": 0,
                }
            )

    if include_volunteer:
        volunteer_rows = list(
            db.session.scalars(
                select(Volunteer)
                .where(Volunteer.organization_id == org_id)
                .order_by(Volunteer.id.asc())
                .limit(max_scan)
            )
        )
        for volunteer in volunteer_rows:
            records.append(
                {
                    "entity_type": "volunteer",
                    "entity_id": int(volunteer.id),
                    "name": str(volunteer.name or "").strip(),
                    "email": _normalize_email(volunteer.email),
                    "phone": _normalize_phone(volunteer.phone),
                    "raw_phone": str(volunteer.phone or "").strip(),
                    "donation_count": 0,
                }
            )

    by_email: dict[str, list[dict[str, Any]]] = {}
    by_name_phone: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        email_key = _normalize_email(record.get("email"))
        if email_key:
            by_email.setdefault(email_key, []).append(record)

        name_key = _normalize_text(record.get("name"))
        phone_key = _normalize_phone(record.get("phone"))
        if name_key and phone_key:
            by_name_phone.setdefault(f"{name_key}::{phone_key}", []).append(record)

    candidate_map: dict[str, dict[str, Any]] = {}

    def _add_candidates(reason: str, key_prefix: str, groups: dict[str, list[dict[str, Any]]]) -> None:
        for key, rows in groups.items():
            if len(rows) < 2:
                continue
            candidate_records = [
                {
                    "entity_type": str(item.get("entity_type") or ""),
                    "entity_id": int(item.get("entity_id") or 0),
                    "name": str(item.get("name") or ""),
                    "email": str(item.get("email") or ""),
                    "phone": str(item.get("raw_phone") or ""),
                    "donation_count": int(item.get("donation_count") or 0),
                }
                for item in rows
            ]
            candidate_records.sort(
                key=lambda row: (
                    str(row.get("entity_type") or ""),
                    int(row.get("entity_id") or 0),
                )
            )
            signature = "|".join(
                f"{row['entity_type']}:{row['entity_id']}"
                for row in candidate_records
            )
            candidate_key = f"{reason}:{signature}"
            mergeable_donors = [r for r in candidate_records if r["entity_type"] == "donor"]
            best_primary = None
            if len(mergeable_donors) >= 2:
                best_primary = sorted(
                    mergeable_donors,
                    key=lambda r: (
                        -int(r.get("donation_count") or 0),
                        int(r.get("entity_id") or 0),
                    ),
                )[0]

            candidate_map[candidate_key] = {
                "reason": reason,
                "match_key": f"{key_prefix}:{key}",
                "confidence": round(_dedupe_confidence(reason, len(candidate_records)), 2),
                "records": candidate_records,
                "record_count": int(len(candidate_records)),
                "merge_supported": bool(len(mergeable_donors) >= 2),
                "suggested_primary_donor_id": int(best_primary["entity_id"]) if best_primary else None,
            }

    _add_candidates("matching_email", "email", by_email)
    _add_candidates("matching_name_phone", "name_phone", by_name_phone)

    candidates = list(candidate_map.values())
    candidates.sort(
        key=lambda row: (
            -float(row.get("confidence") or 0.0),
            -int(row.get("record_count") or 0),
            str(row.get("match_key") or ""),
        )
    )

    return jsonify(
        {
            "organization_id": int(org_id),
            "entity_scope": entity_scope,
            "count": int(len(candidates)),
            "candidates": candidates[:limit],
            "meta": {
                "limit": int(limit),
                "max_scan": int(max_scan),
                "implemented_actions": ["donor_merge", "cross_entity_review"],
            },
        }
    ), 200


@v2_bp.post("/dedupe/workbench/merge")
@login_required
@roles_required("admin", "staff")
def dedupe_workbench_merge_route():
    """Merge duplicate donor records from the dedupe workbench."""
    from ngo_homesuite.services.donor_service import DonorNotFound, DonorService

    data = _json_or_400(["primary_donor_id", "duplicate_donor_id"])
    try:
        primary_donor_id = int(data.get("primary_donor_id") or 0)
        duplicate_donor_id = int(data.get("duplicate_donor_id") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "primary_donor_id and duplicate_donor_id must be integers"}), 400

    if primary_donor_id <= 0 or duplicate_donor_id <= 0 or primary_donor_id == duplicate_donor_id:
        return jsonify({"error": "primary_donor_id and duplicate_donor_id must be distinct positive integers"}), 400

    dry_run = bool(data.get("dry_run", False))
    service = DonorService()
    org_id = _org_id()

    try:
        primary = service.get_donor(primary_donor_id, org_id)
        duplicate = service.get_donor(duplicate_donor_id, org_id)
    except DonorNotFound:
        return jsonify({"error": "donor not found"}), 404

    duplicate_donation_count = int(
        db.session.scalar(
            select(func.count(Donation.id)).where(
                Donation.organization_id == org_id,
                Donation.donor_id == int(duplicate.id),
            )
        )
        or 0
    )

    if dry_run:
        return jsonify(
            {
                "dry_run": True,
                "merge_supported": True,
                "primary": {
                    "id": int(primary.id),
                    "name": str(primary.name or ""),
                    "email": str(primary.email or ""),
                },
                "duplicate": {
                    "id": int(duplicate.id),
                    "name": str(duplicate.name or ""),
                    "email": str(duplicate.email or ""),
                },
                "impact": {
                    "duplicate_donation_count": duplicate_donation_count,
                },
            }
        ), 200

    try:
        merged_primary, merged_duplicate = service.merge_donors(org_id, int(primary.id), int(duplicate.id))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify(
        {
            "merged": True,
            "primary_donor_id": int(merged_primary.id),
            "removed_donor_id": int(merged_duplicate.id),
            "relinked": {
                "donations": duplicate_donation_count,
            },
        }
    ), 200


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


@v2_bp.route("/grants/<int:grant_id>/compliance-package", methods=["GET"])
@login_required
@roles_required("admin", "staff")
def grants_compliance_package(grant_id: int):
    try:
        payload = _grants().build_grant_compliance_package(grant_id, _org_id())
    except GrantNotFound as exc:
        return jsonify({"error": str(exc)}), 404
    return jsonify(payload), 200


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


def _parse_optional_iso_datetime(value: Any, *, field_name: str) -> datetime | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        normalized = raw.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be ISO datetime") from exc
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _project_milestone_dict(milestone) -> dict[str, Any]:
    return {
        "id": int(milestone.id),
        "organization_id": int(milestone.organization_id),
        "project_id": int(milestone.project_id),
        "title": str(milestone.title),
        "description": milestone.description,
        "due_date": milestone.due_date.isoformat() if milestone.due_date else None,
        "status": str(milestone.status or "planned"),
        "owner_user_id": int(milestone.owner_user_id) if milestone.owner_user_id else None,
        "completed_at": milestone.completed_at.isoformat() if milestone.completed_at else None,
        "created_at": milestone.created_at.isoformat() if milestone.created_at else None,
        "updated_at": milestone.updated_at.isoformat() if milestone.updated_at else None,
    }


@v2_bp.route("/projects/<int:project_id>/milestones", methods=["GET"])
@login_required
@roles_required("admin", "staff", "viewer")
def list_project_milestones(project_id: int):
    from ngo_homesuite.models.core import Project, ProjectMilestone

    project = db.session.scalar(
        select(Project).where(Project.id == project_id, Project.organization_id == _org_id()).limit(1)
    )
    if project is None:
        return jsonify({"error": "project not found"}), 404

    milestones = list(
        db.session.scalars(
            select(ProjectMilestone)
            .where(
                ProjectMilestone.organization_id == _org_id(),
                ProjectMilestone.project_id == int(project_id),
            )
            .order_by(ProjectMilestone.due_date.asc(), ProjectMilestone.created_at.asc())
        )
    )
    return jsonify({"count": len(milestones), "milestones": [_project_milestone_dict(item) for item in milestones]})


@v2_bp.route("/projects/<int:project_id>/milestones", methods=["POST"])
@login_required
@roles_required("admin", "staff")
def create_project_milestone(project_id: int):
    from ngo_homesuite.models.core import Project, ProjectMilestone

    project = db.session.scalar(
        select(Project).where(Project.id == project_id, Project.organization_id == _org_id()).limit(1)
    )
    if project is None:
        return jsonify({"error": "project not found"}), 404

    data = _json_or_400(required=["title"])
    title = str(data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title is required"}), 400

    status = str(data.get("status") or "planned").strip().lower()
    if status not in {"planned", "in_progress", "completed", "blocked"}:
        return jsonify({"error": "status must be one of: planned, in_progress, completed, blocked"}), 400

    try:
        due_date = _parse_optional_iso_datetime(data.get("due_date"), field_name="due_date")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    owner_user_id = data.get("owner_user_id")
    if owner_user_id is not None:
        try:
            owner_user_id = int(owner_user_id)
        except (TypeError, ValueError):
            return jsonify({"error": "owner_user_id must be an integer"}), 400

    completed_at = _utcnow_naive() if status == "completed" else None
    milestone = ProjectMilestone(
        organization_id=_org_id(),
        project_id=int(project_id),
        title=title,
        description=(str(data.get("description") or "").strip() or None),
        due_date=due_date,
        status=status,
        owner_user_id=owner_user_id,
        completed_at=completed_at,
    )
    db.session.add(milestone)
    db.session.commit()
    return jsonify(_project_milestone_dict(milestone)), 201


@v2_bp.route("/projects/<int:project_id>/milestones/<int:milestone_id>", methods=["PATCH"])
@login_required
@roles_required("admin", "staff")
def update_project_milestone(project_id: int, milestone_id: int):
    from ngo_homesuite.models.core import ProjectMilestone

    milestone = db.session.scalar(
        select(ProjectMilestone).where(
            ProjectMilestone.id == milestone_id,
            ProjectMilestone.project_id == project_id,
            ProjectMilestone.organization_id == _org_id(),
        ).limit(1)
    )
    if milestone is None:
        return jsonify({"error": "milestone not found"}), 404

    data = _json_or_400()

    if "title" in data:
        title = str(data.get("title") or "").strip()
        if not title:
            return jsonify({"error": "title cannot be empty"}), 400
        milestone.title = title
    if "description" in data:
        milestone.description = (str(data.get("description") or "").strip() or None)
    if "status" in data:
        status = str(data.get("status") or "").strip().lower()
        if status not in {"planned", "in_progress", "completed", "blocked"}:
            return jsonify({"error": "status must be one of: planned, in_progress, completed, blocked"}), 400
        milestone.status = status
        milestone.completed_at = _utcnow_naive() if status == "completed" else None
    if "owner_user_id" in data:
        owner_raw = data.get("owner_user_id")
        if owner_raw in (None, ""):
            milestone.owner_user_id = None
        else:
            try:
                milestone.owner_user_id = int(owner_raw)
            except (TypeError, ValueError):
                return jsonify({"error": "owner_user_id must be an integer"}), 400
    if "due_date" in data:
        try:
            milestone.due_date = _parse_optional_iso_datetime(data.get("due_date"), field_name="due_date")
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    db.session.commit()
    return jsonify(_project_milestone_dict(milestone)), 200


@v2_bp.route("/tasks/<int:task_id>/dependencies", methods=["POST"])
@login_required
@roles_required("admin", "staff")
def create_task_dependency(task_id: int):
    from ngo_homesuite.models.core import Task, TaskDependency

    task = db.session.scalar(
        select(Task).where(Task.id == task_id, Task.organization_id == _org_id()).limit(1)
    )
    if task is None:
        return jsonify({"error": "task not found"}), 404

    data = _json_or_400(required=["depends_on_task_id"])
    try:
        depends_on_task_id = int(data.get("depends_on_task_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "depends_on_task_id must be an integer"}), 400

    if int(task_id) == int(depends_on_task_id):
        return jsonify({"error": "task cannot depend on itself"}), 400

    depends_on = db.session.scalar(
        select(Task).where(Task.id == depends_on_task_id, Task.organization_id == _org_id()).limit(1)
    )
    if depends_on is None:
        return jsonify({"error": "depends_on task not found"}), 404
    if task.project_id and depends_on.project_id and int(task.project_id) != int(depends_on.project_id):
        return jsonify({"error": "dependency tasks must belong to the same project"}), 400

    dependency_type = str(data.get("dependency_type") or "blocks").strip().lower()
    if dependency_type not in {"blocks", "related"}:
        return jsonify({"error": "dependency_type must be one of: blocks, related"}), 400

    dependency = TaskDependency(
        organization_id=_org_id(),
        task_id=int(task.id),
        depends_on_task_id=int(depends_on.id),
        dependency_type=dependency_type,
    )
    db.session.add(dependency)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "dependency already exists"}), 409

    return jsonify(
        {
            "id": int(dependency.id),
            "task_id": int(dependency.task_id),
            "depends_on_task_id": int(dependency.depends_on_task_id),
            "dependency_type": str(dependency.dependency_type),
            "created_at": dependency.created_at.isoformat() if dependency.created_at else None,
        }
    ), 201


@v2_bp.route("/tasks/<int:task_id>/dependencies/<int:depends_on_task_id>", methods=["DELETE"])
@login_required
@roles_required("admin", "staff")
def delete_task_dependency(task_id: int, depends_on_task_id: int):
    from ngo_homesuite.models.core import TaskDependency

    dependency = db.session.scalar(
        select(TaskDependency).where(
            TaskDependency.organization_id == _org_id(),
            TaskDependency.task_id == int(task_id),
            TaskDependency.depends_on_task_id == int(depends_on_task_id),
        ).limit(1)
    )
    if dependency is None:
        return jsonify({"error": "dependency not found"}), 404

    db.session.delete(dependency)
    db.session.commit()
    return jsonify({"removed": True, "task_id": int(task_id), "depends_on_task_id": int(depends_on_task_id)}), 200


@v2_bp.route("/projects/<int:project_id>/board", methods=["GET"])
@login_required
@roles_required("admin", "staff", "viewer")
def project_board(project_id: int):
    from ngo_homesuite.models.core import Project, ProjectMilestone, Task, TaskDependency

    project = db.session.scalar(
        select(Project).where(Project.id == int(project_id), Project.organization_id == _org_id()).limit(1)
    )
    if project is None:
        return jsonify({"error": "project not found"}), 404

    tasks = list(
        db.session.scalars(
            select(Task)
            .where(Task.organization_id == _org_id(), Task.project_id == int(project_id))
            .order_by(Task.status.asc(), Task.priority.asc(), Task.created_at.asc())
        )
    )
    labels = _task_labels(_org_id(), tasks)

    task_ids = [int(task.id) for task in tasks]
    dependencies = []
    dependency_by_task: dict[int, list[dict[str, Any]]] = defaultdict(list)
    blocked_task_ids: set[int] = set()
    prerequisite_ids: set[int] = set()

    if task_ids:
        dependency_rows = list(
            db.session.scalars(
                select(TaskDependency).where(
                    TaskDependency.organization_id == _org_id(),
                    TaskDependency.task_id.in_(task_ids),
                )
            )
        )
        prerequisite_ids = {int(row.depends_on_task_id) for row in dependency_rows}
        prerequisite_map = {
            int(row.id): row
            for row in db.session.scalars(
                select(Task).where(Task.organization_id == _org_id(), Task.id.in_(prerequisite_ids))
            )
        }

        for row in dependency_rows:
            prereq = prerequisite_map.get(int(row.depends_on_task_id))
            dependency_payload = {
                "depends_on_task_id": int(row.depends_on_task_id),
                "dependency_type": str(row.dependency_type),
                "depends_on_status": str(prereq.status) if prereq is not None else None,
                "is_blocking": bool(
                    row.dependency_type == "blocks"
                    and prereq is not None
                    and str(prereq.status or "").lower() != "done"
                ),
            }
            dependency_by_task[int(row.task_id)].append(dependency_payload)
            dependencies.append({"task_id": int(row.task_id), **dependency_payload})
            if dependency_payload["is_blocking"]:
                blocked_task_ids.add(int(row.task_id))

    milestones = list(
        db.session.scalars(
            select(ProjectMilestone)
            .where(
                ProjectMilestone.organization_id == _org_id(),
                ProjectMilestone.project_id == int(project_id),
            )
            .order_by(ProjectMilestone.due_date.asc(), ProjectMilestone.created_at.asc())
        )
    )

    task_payload = []
    for task in tasks:
        item = _task_dict(task, labels=labels)
        item["dependencies"] = dependency_by_task.get(int(task.id), [])
        item["blocked"] = int(task.id) in blocked_task_ids
        task_payload.append(item)

    status_counts: dict[str, int] = defaultdict(int)
    for task in tasks:
        status_counts[str(task.status or "open")] += 1

    milestone_completed = sum(1 for milestone in milestones if str(milestone.status or "").lower() == "completed")

    return jsonify(
        {
            "project": {
                "id": int(project.id),
                "name": str(project.name),
                "status": str(project.status or "planned"),
            },
            "summary": {
                "total_tasks": len(tasks),
                "blocked_tasks": len(blocked_task_ids),
                "status_counts": dict(status_counts),
                "milestones_total": len(milestones),
                "milestones_completed": milestone_completed,
            },
            "tasks": task_payload,
            "dependencies": dependencies,
            "milestones": [_project_milestone_dict(item) for item in milestones],
        }
    )


@v2_bp.route("/tasks/reminder-candidates", methods=["GET"])
@login_required
def task_reminder_candidates():
    from ngo_homesuite.services.reminder_service import recommend_task_reminders

    limit = request.args.get("limit", 25, type=int)
    payload = recommend_task_reminders(_org_id(), limit=max(1, min(limit, 200)))
    return jsonify(payload)


@v2_bp.route("/task-assignees", methods=["GET"])
@login_required
@roles_required("admin", "staff")
def task_assignees():
    from ngo_homesuite.models.core import User

    users = list(db.session.scalars(select(User).where(User.organization_id == _org_id()).order_by(User.created_at.asc())))
    return jsonify([
        {
            "id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "role": user.role,
            "is_active": user.is_active,
        }
        for user in users
    ])


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


@v2_bp.route("/membership/members", methods=["GET"])
@login_required
@roles_required("admin", "staff")
def membership_members_list():
    from ngo_homesuite.services.membership_service import list_members_page as svc

    status = (request.args.get("status") or "").strip() or None
    q = (request.args.get("q") or "").strip() or None
    tier_id_raw = request.args.get("tier_id")
    expiring_raw = request.args.get("expiring_within_days")
    page = request.args.get("page", 1, type=int)
    page_size = request.args.get("page_size", 25, type=int)

    tier_id = None
    if tier_id_raw not in (None, ""):
        try:
            tier_id = int(tier_id_raw)
        except (TypeError, ValueError):
            return jsonify({"error": "tier_id must be an integer"}), 400

    expiring_within_days = None
    if expiring_raw not in (None, ""):
        try:
            expiring_within_days = int(expiring_raw)
        except (TypeError, ValueError):
            return jsonify({"error": "expiring_within_days must be an integer"}), 400

    safe_page = max(1, int(page or 1))
    safe_size = max(1, min(int(page_size or 25), 200))
    rows, total = svc(
        _org_id(),
        status=status,
        tier_id=tier_id,
        search_query=q,
        expiring_within_days=expiring_within_days,
        limit=safe_size,
        offset=(safe_page - 1) * safe_size,
    )
    return jsonify(
        {
            "items": [
                {
                    "id": int(record.id),
                    "donor_id": int(record.donor_id),
                    "tier_id": int(record.tier_id),
                    "status": str(record.status or ""),
                    "start_date": record.start_date.isoformat() if record.start_date else None,
                    "end_date": record.end_date.isoformat() if record.end_date else None,
                    "next_renewal_date": record.next_renewal_date.isoformat() if record.next_renewal_date else None,
                    "donor_name": str(getattr(record.donor, "name", "") or ""),
                    "donor_email": str(getattr(record.donor, "email", "") or ""),
                    "tier_name": str(getattr(record.tier, "name", "") or ""),
                }
                for record in rows
            ],
            "pagination": {
                "page": safe_page,
                "page_size": safe_size,
                "total": int(total),
            },
        }
    ), 200


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


@v2_bp.route("/donors/<int:donor_id>/journey", methods=["GET"])
@login_required
def get_donor_journey(donor_id: int):
    """Return a rich 360-degree donor journey snapshot and timeline."""
    from ngo_homesuite.services.activity_timeline_service import ActivityTimelineService

    limit = request.args.get("limit", 200, type=int)
    offset = request.args.get("offset", 0, type=int)
    search_query = (request.args.get("q") or "").strip() or None

    limit = max(1, min(limit, 1000))
    offset = max(offset, 0)

    payload = ActivityTimelineService.get_donor_journey(
        _org_id(),
        donor_id,
        limit=limit,
        offset=offset,
        search_query=search_query,
    )
    if payload is None:
        return jsonify({"error": "donor not found"}), 404
    return jsonify(payload)


@v2_bp.route("/donor-journeys/automations/run", methods=["POST"])
@login_required
@roles_required("admin", "staff")
def run_donor_journey_automations_route():
    from ngo_homesuite.services.stewardship_service import run_donor_journey_automations

    data = request.get_json(silent=True) or {}

    def _safe_int(name: str, default: int, minimum: int, maximum: int) -> tuple[int | None, str | None]:
        raw = data.get(name, default)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return None, f"{name} must be an integer"
        if value < minimum or value > maximum:
            return None, f"{name} must be between {minimum} and {maximum}"
        return value, None

    lapsing_days, err = _safe_int("lapsing_days", 120, 30, 3650)
    if err:
        return jsonify({"error": err}), 400
    first_gift_window_days, err = _safe_int("first_gift_window_days", 7, 1, 90)
    if err:
        return jsonify({"error": err}), 400
    recurring_fail_threshold, err = _safe_int("recurring_fail_threshold", 2, 1, 20)
    if err:
        return jsonify({"error": err}), 400
    cooldown_days, err = _safe_int("cooldown_days", 21, 1, 365)
    if err:
        return jsonify({"error": err}), 400

    payload = run_donor_journey_automations(
        _org_id(),
        actor_user_id=int(getattr(current_user, "id", 0) or 0) or None,
        lapsing_days=int(lapsing_days),
        first_gift_window_days=int(first_gift_window_days),
        recurring_fail_threshold=int(recurring_fail_threshold),
        cooldown_days=int(cooldown_days),
    )
    return jsonify(payload), 200


@v2_bp.route("/donor-journeys/automations/events", methods=["GET"])
@login_required
@roles_required("admin", "staff", "viewer")
def list_donor_journey_automation_events_route():
    from ngo_homesuite.services.stewardship_service import list_donor_journey_automation_events

    trigger_name = str(request.args.get("trigger") or "").strip() or None
    status = str(request.args.get("status") or "").strip() or None
    limit = request.args.get("limit", default=100, type=int)

    payload = list_donor_journey_automation_events(
        _org_id(),
        trigger_name=trigger_name,
        status=status,
        limit=int(limit or 100),
    )
    return jsonify(payload), 200


@v2_bp.route("/forms/submissions/public", methods=["POST"])
def ingest_form_submission_public():
    from ngo_homesuite.services.form_ecosystem_service import FormEcosystemService

    expected_token = _form_ingest_token()
    if not expected_token:
        return jsonify({"error": "Form ingest token is not configured"}), 503

    provided_token = _request_ingest_token()
    if not provided_token or provided_token != expected_token:
        return jsonify({"error": "Invalid ingest token"}), 401

    data = _json_or_400(required=["organization_id", "source", "form_type"])
    try:
        org_id = int(data.get("organization_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "organization_id must be an integer"}), 400

    org = db.session.scalar(
        select(Organization).where(
            Organization.id == org_id,
            Organization.is_active.is_(True),
        ).limit(1)
    )
    if org is None:
        return jsonify({"error": "organization not found"}), 404

    # Public ingestion is tenant-targeted by payload, not by authenticated session.
    g.organization_id = int(org_id)

    try:
        result = FormEcosystemService.submit_form(
            org_id=org_id,
            source=str(data.get("source") or ""),
            form_type=str(data.get("form_type") or ""),
            payload=data,
            actor_user_id=None,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify(result), (200 if result.get("duplicate") else 201)


@v2_bp.route("/forms/submissions", methods=["POST"])
@login_required
@roles_required("admin", "staff")
def ingest_form_submission_internal():
    from ngo_homesuite.services.form_ecosystem_service import FormEcosystemService

    data = _json_or_400(required=["source", "form_type"])
    try:
        result = FormEcosystemService.submit_form(
            org_id=_org_id(),
            source=str(data.get("source") or ""),
            form_type=str(data.get("form_type") or ""),
            payload=data,
            actor_user_id=int(getattr(current_user, "id", 0) or 0) or None,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify(result), (200 if result.get("duplicate") else 201)


@v2_bp.route("/forms/submissions", methods=["GET"])
@login_required
@roles_required("admin", "staff", "viewer")
def list_integrated_form_submissions():
    from ngo_homesuite.services.form_ecosystem_service import FormEcosystemService

    source = str(request.args.get("source") or "").strip().lower() or None
    form_type = str(request.args.get("form_type") or "").strip().lower() or None
    status = str(request.args.get("status") or "").strip().lower() or None
    limit = request.args.get("limit", default=100, type=int)

    payload = FormEcosystemService.list_submissions(
        org_id=_org_id(),
        source=source,
        form_type=form_type,
        status=status,
        limit=int(limit or 100),
    )
    return jsonify(payload), 200


@v2_bp.route("/donations/<int:donation_id>/soft-credits", methods=["GET"])
@login_required
def list_donation_soft_credits(donation_id: int):
    """List relational soft-credit attribution records for a donation."""
    donation = db.session.scalar(
        select(Donation).where(
            Donation.id == donation_id,
            Donation.organization_id == _org_id(),
        ).limit(1)
    )
    if donation is None:
        return jsonify({"error": "donation not found"}), 404

    rows = (
        db.session.query(DonorSoftCredit)
        .filter(
            DonorSoftCredit.organization_id == _org_id(),
            DonorSoftCredit.donation_id == int(donation_id),
        )
        .order_by(DonorSoftCredit.created_at.desc(), DonorSoftCredit.id.desc())
        .all()
    )

    return jsonify(
        [
            {
                "id": int(row.id),
                "organization_id": int(row.organization_id),
                "donation_id": int(row.donation_id),
                "donor_id": int(row.donor_id),
                "role": str(row.role),
                "credited_amount": float(row.credited_amount or 0.0),
                "credit_weight": float(row.credit_weight or 1.0),
                "rationale": row.rationale,
                "attributed_by_user_id": int(row.attributed_by_user_id) if row.attributed_by_user_id else None,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
    )


@v2_bp.route("/donations/<int:donation_id>/soft-credits", methods=["POST"])
@login_required
@roles_required("admin", "staff")
def create_donation_soft_credit(donation_id: int):
    """Create an explicit soft-credit attribution record for a donation."""
    data = _json_or_400(required=["donor_id"])

    donation = db.session.scalar(
        select(Donation).where(
            Donation.id == donation_id,
            Donation.organization_id == _org_id(),
        ).limit(1)
    )
    if donation is None:
        return jsonify({"error": "donation not found"}), 404

    influencer_donor_id = int(data.get("donor_id"))
    influencer = db.session.scalar(
        select(Donor).where(
            Donor.id == influencer_donor_id,
            Donor.organization_id == _org_id(),
        ).limit(1)
    )
    if influencer is None:
        return jsonify({"error": "influencer donor not found"}), 404

    role = str(data.get("role") or "influencer").strip().lower()
    if role not in {"influencer", "solicitor", "steward"}:
        return jsonify({"error": "role must be one of: influencer, solicitor, steward"}), 400

    credited_amount_raw = data.get("credited_amount", donation.amount)
    credit_weight_raw = data.get("credit_weight", 1.0)
    try:
        credited_amount = float(credited_amount_raw)
    except (TypeError, ValueError):
        return jsonify({"error": "credited_amount must be numeric"}), 400
    try:
        credit_weight = float(credit_weight_raw)
    except (TypeError, ValueError):
        return jsonify({"error": "credit_weight must be numeric"}), 400

    if credited_amount < 0:
        return jsonify({"error": "credited_amount must be >= 0"}), 400
    if credit_weight <= 0:
        return jsonify({"error": "credit_weight must be > 0"}), 400

    soft_credit = DonorSoftCredit(
        organization_id=_org_id(),
        donation_id=int(donation.id),
        donor_id=int(influencer.id),
        role=role,
        credited_amount=credited_amount,
        credit_weight=credit_weight,
        rationale=(str(data.get("rationale") or "").strip() or None),
        attributed_by_user_id=int(getattr(current_user, "id", 0) or 0) or None,
    )
    db.session.add(soft_credit)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "soft credit already exists for this donor/donation/role"}), 409

    return jsonify(
        {
            "id": int(soft_credit.id),
            "organization_id": int(soft_credit.organization_id),
            "donation_id": int(soft_credit.donation_id),
            "donor_id": int(soft_credit.donor_id),
            "role": soft_credit.role,
            "credited_amount": float(soft_credit.credited_amount or 0.0),
            "credit_weight": float(soft_credit.credit_weight or 1.0),
            "rationale": soft_credit.rationale,
            "attributed_by_user_id": int(soft_credit.attributed_by_user_id) if soft_credit.attributed_by_user_id else None,
            "created_at": soft_credit.created_at.isoformat() if soft_credit.created_at else None,
        }
    ), 201


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


@v2_bp.route("/intelligence/dashboard", methods=["GET"])
@login_required
@roles_required("admin", "staff", "viewer")
def role_based_dashboard_intelligence():
    from ngo_homesuite.services.reporting_service import ReportingService

    period = str(request.args.get("period", "30d") or "30d").strip().lower()
    start_date_raw = str(request.args.get("start_date") or "").strip()
    end_date_raw = str(request.args.get("end_date") or "").strip()

    start_date = None
    end_date = None
    if start_date_raw:
        try:
            start_date = datetime.combine(_parse_iso_date(start_date_raw), datetime.min.time())
        except ValueError:
            return jsonify({"error": "start_date must be ISO format YYYY-MM-DD"}), 400
    if end_date_raw:
        try:
            end_date = datetime.combine(_parse_iso_date(end_date_raw), datetime.min.time())
        except ValueError:
            return jsonify({"error": "end_date must be ISO format YYYY-MM-DD"}), 400

    actor_role = str(getattr(current_user, "role", "viewer") or "viewer").strip().lower()
    preview_role = str(request.args.get("role") or "").strip().lower()
    effective_role = actor_role
    if preview_role:
        if actor_role != "admin":
            return jsonify({"error": "Only admin users may preview alternate intelligence roles"}), 403
        effective_role = preview_role

    payload = ReportingService().role_based_intelligence(
        _org_id(),
        role=effective_role,
        actor_user_id=int(getattr(current_user, "id", 0) or 0) or None,
        period=period,
        start_date=start_date,
        end_date=end_date,
    )
    return jsonify(payload)


@v2_bp.route("/intelligence/financial-guardrails", methods=["GET"])
@login_required
@roles_required("admin", "staff", "viewer")
def financial_guardrails_intelligence():
    from ngo_homesuite.services.reporting_service import ReportingService

    period = str(request.args.get("period", "30d") or "30d").strip().lower()
    start_date_raw = str(request.args.get("start_date") or "").strip()
    end_date_raw = str(request.args.get("end_date") or "").strip()

    start_date = None
    end_date = None
    if start_date_raw:
        try:
            start_date = datetime.combine(_parse_iso_date(start_date_raw), datetime.min.time())
        except ValueError:
            return jsonify({"error": "start_date must be ISO format YYYY-MM-DD"}), 400
    if end_date_raw:
        try:
            end_date = datetime.combine(_parse_iso_date(end_date_raw), datetime.min.time())
        except ValueError:
            return jsonify({"error": "end_date must be ISO format YYYY-MM-DD"}), 400

    actor_role = str(getattr(current_user, "role", "viewer") or "viewer").strip().lower()
    preview_role = str(request.args.get("role") or "").strip().lower()
    effective_role = actor_role
    if preview_role:
        if actor_role != "admin":
            return jsonify({"error": "Only admin users may preview alternate intelligence roles"}), 403
        effective_role = preview_role

    payload = ReportingService().financial_guardrails_intelligence(
        _org_id(),
        role=effective_role,
        actor_user_id=int(getattr(current_user, "id", 0) or 0) or None,
        period=period,
        start_date=start_date,
        end_date=end_date,
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


@v2_bp.route("/campaigns/<int:campaign_id>/emails/deliverability", methods=["POST"])
@login_required
@roles_required("admin", "staff")
def campaign_email_deliverability_route(campaign_id: int):
    """Run deliverability preflight checks for campaign email content and audience."""
    from ngo_homesuite.services.campaign_email_service import campaign_email_deliverability_report

    data = request.get_json(silent=True) or {}
    try:
        payload = campaign_email_deliverability_report(
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


@v2_bp.route("/campaigns/<int:campaign_id>/emails/automation/sequence", methods=["POST"])
@login_required
@roles_required("admin", "staff")
def campaign_email_automation_sequence_route(campaign_id: int):
    """Schedule a multi-step campaign drip sequence using existing scheduled-batch infrastructure."""
    from ngo_homesuite.services.campaign_email_service import schedule_campaign_email_sequence

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

    try:
        step_count = int(data.get("step_count", 3))
        cadence_days = int(data.get("cadence_days", 7))
    except (TypeError, ValueError):
        return jsonify({"error": "step_count and cadence_days must be integers"}), 400

    if step_count <= 0:
        return jsonify({"error": "step_count must be at least 1"}), 400
    if cadence_days <= 0:
        return jsonify({"error": "cadence_days must be at least 1"}), 400

    scheduled_start = None
    scheduled_at_raw = data.get("start_at")
    if scheduled_at_raw:
        from datetime import datetime as _dt
        try:
            scheduled_start = _dt.fromisoformat(str(scheduled_at_raw).replace("Z", "+00:00")).replace(tzinfo=None)
        except (TypeError, ValueError):
            return jsonify({"error": "start_at must be an ISO datetime"}), 400

    audience_payload = data.get("audience") if isinstance(data.get("audience"), dict) else {}
    audience_payload = dict(audience_payload)
    audience_payload["_human_in_the_loop"] = hitl_metadata

    try:
        payload = schedule_campaign_email_sequence(
            _org_id(),
            campaign_id,
            created_by_user_id=int(getattr(current_user, "id", 0) or 0),
            created_by_username=str(getattr(current_user, "username", "") or ""),
            created_by_role=str(getattr(current_user, "role", "") or ""),
            subject=str(data.get("subject") or ""),
            body=str(data.get("body") or ""),
            audience=audience_payload,
            human_authorization=hitl_metadata,
            step_count=step_count,
            cadence_days=cadence_days,
            start_at=scheduled_start,
        )
    except LookupError:
        return jsonify({"error": "Campaign not found"}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify(payload), 200


@v2_bp.route("/campaigns/<int:campaign_id>/emails/automation/templates", methods=["GET"])
@login_required
@roles_required("admin", "staff")
def campaign_email_automation_templates_route(campaign_id: int):
    """List built-in automation templates available for a campaign."""
    from ngo_homesuite.services.campaign_email_service import campaign_email_automation_templates
    from ngo_homesuite.services.campaign_service import get_campaign

    campaign = get_campaign(campaign_id, _org_id())
    if campaign is None:
        return jsonify({"error": "Campaign not found"}), 404
    return jsonify(campaign_email_automation_templates(campaign)), 200


@v2_bp.route("/campaigns/<int:campaign_id>/emails/automation/templates/<string:template_key>/instantiate", methods=["POST"])
@login_required
@roles_required("admin", "staff")
def campaign_email_automation_template_instantiate_route(campaign_id: int, template_key: str):
    """Instantiate and schedule a built-in campaign automation template."""
    from ngo_homesuite.services.campaign_email_service import instantiate_campaign_email_automation_template

    actor_role = str(getattr(current_user, "role", "") or "").strip().lower()
    actor_granted = bool(getattr(current_user, "can_authorize_external_comms", False))
    if actor_role != "admin" and not actor_granted:
        return jsonify(
            {
                "error": "User is not authorized for outbound external communications.",
                "required_permission": "can_authorize_external_comms",
            }
        ), 403

    data = request.get_json(silent=True) or {}
    hitl_metadata, hitl_error = _human_in_the_loop_metadata(data)
    if hitl_error:
        return jsonify({
            "error": hitl_error,
            "warning": "All outbound external communication requires explicit human authorization.",
            "human_in_the_loop_required": True,
        }), 400

    scheduled_start = None
    scheduled_at_raw = data.get("start_at")
    if scheduled_at_raw:
        from datetime import datetime as _dt
        try:
            scheduled_start = _dt.fromisoformat(str(scheduled_at_raw).replace("Z", "+00:00")).replace(tzinfo=None)
        except (TypeError, ValueError):
            return jsonify({"error": "start_at must be an ISO datetime"}), 400

    audience_payload = data.get("audience") if isinstance(data.get("audience"), dict) else {}
    audience_payload = dict(audience_payload)
    audience_payload["_human_in_the_loop"] = hitl_metadata

    try:
        payload = instantiate_campaign_email_automation_template(
            _org_id(),
            campaign_id,
            template_key=str(template_key or ""),
            created_by_user_id=int(getattr(current_user, "id", 0) or 0),
            created_by_username=str(getattr(current_user, "username", "") or ""),
            created_by_role=str(getattr(current_user, "role", "") or ""),
            audience=audience_payload,
            human_authorization=hitl_metadata,
            start_at=scheduled_start,
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


@v2_bp.route("/campaigns/<int:campaign_id>/emails/attribution", methods=["GET"])
@login_required
@roles_required("admin", "staff")
def campaign_email_attribution_route(campaign_id: int):
    """Return donation attribution metrics influenced by campaign email touches."""
    from ngo_homesuite.services.campaign_email_service import campaign_email_attribution

    window_raw = request.args.get("window_days", "30")
    try:
        window_days = int(window_raw)
    except (TypeError, ValueError):
        return jsonify({"error": "window_days must be an integer"}), 400

    try:
        payload = campaign_email_attribution(_org_id(), campaign_id, window_days=window_days)
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


@v2_bp.patch("/campaigns/email/segments/<int:segment_id>")
@login_required
@roles_required("admin", "staff")
def campaign_email_segments_update_route(segment_id: int):
    """Update an existing saved campaign email segment."""
    from werkzeug.exceptions import NotFound

    from ngo_homesuite.services.smart_groups_service import evaluate_group, update_group

    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "").strip() if "name" in data else None
    description = str(data.get("description") or "") if "description" in data else None
    rules = data.get("rules") if "rules" in data else None
    include_preview = bool(data.get("include_preview", False))

    try:
        group = update_group(
            int(segment_id),
            _org_id(),
            name=name,
            description=description,
            rules=rules,
        )
    except NotFound:
        return jsonify({"error": "Segment not found"}), 404
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
        payload["members"] = members[:50]

    return jsonify(payload), 200


@v2_bp.delete("/campaigns/email/segments/<int:segment_id>")
@login_required
@roles_required("admin", "staff")
@require_step_up_auth
def campaign_email_segments_delete_route(segment_id: int):
    """Delete a saved campaign email segment."""
    from werkzeug.exceptions import NotFound

    from ngo_homesuite.services.smart_groups_service import delete_group

    try:
        delete_group(int(segment_id), _org_id())
    except NotFound:
        return jsonify({"error": "Segment not found"}), 404
    return jsonify({"deleted": True, "segment_id": int(segment_id)}), 200


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


@v2_bp.post("/campaigns/<int:campaign_id>/emails/queue/process")
@login_required
@roles_required("admin", "staff")
def campaign_email_queue_process_route(campaign_id: int):
    from ngo_homesuite.services.campaign_email_service import process_scheduled_campaign_email_batches

    _ = _get_campaign_or_404(campaign_id)
    limit = request.args.get("limit", 100, type=int)
    result = process_scheduled_campaign_email_batches(limit=max(1, min(int(limit or 100), 500)))
    return jsonify(result), 200


@v2_bp.get("/campaigns/<int:campaign_id>/emails/queue")
@login_required
@roles_required("admin", "staff")
def campaign_email_queue_route(campaign_id: int):
    from ngo_homesuite.services.campaign_email_service import campaign_email_queue_overview

    status = (request.args.get("status") or "").strip() or None
    limit = request.args.get("limit", 25, type=int)
    try:
        payload = campaign_email_queue_overview(
            _org_id(),
            campaign_id,
            status=status,
            limit=max(1, min(int(limit or 25), 200)),
        )
    except LookupError:
        return jsonify({"error": "Campaign not found"}), 404
    return jsonify(payload), 200


@v2_bp.post("/campaigns/<int:campaign_id>/emails/batches/<int:batch_id>/retry-failed")
@login_required
@roles_required("admin", "staff")
def campaign_email_retry_failed_route(campaign_id: int, batch_id: int):
    from ngo_homesuite.services.campaign_email_service import retry_failed_campaign_email_batch

    data = request.get_json(silent=True) or {}
    hitl_metadata, hitl_error = _human_in_the_loop_metadata(data)
    if hitl_error:
        return jsonify(
            {
                "error": hitl_error,
                "warning": "All outbound external communication requires explicit human authorization.",
                "human_in_the_loop_required": True,
            }
        ), 400

    try:
        payload = retry_failed_campaign_email_batch(
            _org_id(),
            campaign_id,
            batch_id,
            created_by_user_id=int(getattr(current_user, "id", 0) or 0),
            created_by_username=str(getattr(current_user, "username", "") or ""),
            created_by_role=str(getattr(current_user, "role", "") or ""),
            human_authorization=hitl_metadata,
        )
    except LookupError:
        return jsonify({"error": "Batch not found"}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(payload), 200


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
    from ngo_homesuite.services.campaign_email_service import upsert_campaign_communication_preference, verify_unsub_signature
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

    try:
        upsert_campaign_communication_preference(
            org_id,
            email=email,
            donor_id=donor_id if donor_id > 0 else None,
            campaign_opt_in=False,
            source="unsubscribe_link",
        )
    except Exception:
        db.session.rollback()

    return (
        "<html><head><title>Unsubscribed</title>"
        "<style>body{font-family:Arial,sans-serif;max-width:480px;margin:80px auto;text-align:center;}</style></head>"
        "<body><h2>You have been unsubscribed.</h2>"
        "<p>You will no longer receive campaign emails from this organization.</p>"
        "</body></html>"
    ), 200


@v2_bp.route("/campaigns/email/preferences", methods=["GET", "POST", "PATCH"])
def campaign_email_preferences_public_route():
    """Public, signed preference-center endpoint used by outbound campaign links."""
    from ngo_homesuite.services.campaign_email_service import (
        get_campaign_communication_preference,
        upsert_campaign_communication_preference,
        verify_preference_signature,
    )

    body = request.get_json(silent=True) or {}
    email = str(request.args.get("email") or body.get("email") or "").strip().lower()
    org_id_raw = request.args.get("organization_id") or body.get("organization_id") or "0"
    donor_id_raw = request.args.get("donor_id") or body.get("donor_id") or "0"
    ts_raw = request.args.get("ts") or body.get("ts") or "0"
    sig = str(request.args.get("sig") or body.get("sig") or "").strip()

    try:
        organization_id = int(org_id_raw)
        donor_id = int(donor_id_raw)
        issued_at = int(ts_raw)
    except (TypeError, ValueError):
        return jsonify({"error": "invalid signed preference link"}), 400

    if organization_id <= 0 or not email or not sig:
        return jsonify({"error": "invalid signed preference link"}), 400

    if not verify_preference_signature(
        email=email,
        organization_id=organization_id,
        donor_id=donor_id,
        issued_at=issued_at,
        signature=sig,
    ):
        return jsonify({"error": "invalid or expired preference link"}), 400

    if request.method == "GET":
        payload = get_campaign_communication_preference(
            organization_id,
            email=email,
            donor_id=donor_id if donor_id > 0 else None,
        )
        payload["signed"] = {
            "email": email,
            "organization_id": organization_id,
            "donor_id": donor_id,
            "ts": issued_at,
        }
        return jsonify(payload), 200

    try:
        payload = upsert_campaign_communication_preference(
            organization_id,
            email=email,
            donor_id=donor_id if donor_id > 0 else None,
            newsletter_opt_in=_bool_or_none(body.get("newsletter_opt_in"), field_name="newsletter_opt_in"),
            campaign_opt_in=_bool_or_none(body.get("campaign_opt_in"), field_name="campaign_opt_in"),
            events_opt_in=_bool_or_none(body.get("events_opt_in"), field_name="events_opt_in"),
            volunteer_opt_in=_bool_or_none(body.get("volunteer_opt_in"), field_name="volunteer_opt_in"),
            digest_frequency=str(body.get("digest_frequency") or "").strip().lower() or None,
            source="public_preference_center",
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify(payload), 200


@v2_bp.get("/campaigns/email/preferences/donors/<int:donor_id>")
@login_required
@roles_required("admin", "staff")
def campaign_email_preferences_donor_get_route(donor_id: int):
    """Authenticated preference lookup for internal campaign/newsletter operations."""
    from ngo_homesuite.services.campaign_email_service import get_campaign_communication_preference

    donor = db.session.get(Donor, int(donor_id))
    if donor is None or int(donor.organization_id) != _org_id():
        return jsonify({"error": "Donor not found"}), 404
    if not donor.email:
        return jsonify({"error": "Donor does not have an email address"}), 400

    payload = get_campaign_communication_preference(
        _org_id(),
        email=str(donor.email),
        donor_id=int(donor.id),
    )
    payload["donor"] = {"id": int(donor.id), "name": str(donor.name or "")}
    return jsonify(payload), 200


@v2_bp.patch("/campaigns/email/preferences/donors/<int:donor_id>")
@login_required
@roles_required("admin", "staff")
def campaign_email_preferences_donor_patch_route(donor_id: int):
    """Authenticated preference update for internal campaign/newsletter operations."""
    from ngo_homesuite.services.campaign_email_service import upsert_campaign_communication_preference

    donor = db.session.get(Donor, int(donor_id))
    if donor is None or int(donor.organization_id) != _org_id():
        return jsonify({"error": "Donor not found"}), 404
    if not donor.email:
        return jsonify({"error": "Donor does not have an email address"}), 400

    data = request.get_json(silent=True) or {}
    try:
        payload = upsert_campaign_communication_preference(
            _org_id(),
            email=str(donor.email),
            donor_id=int(donor.id),
            newsletter_opt_in=_bool_or_none(data.get("newsletter_opt_in"), field_name="newsletter_opt_in"),
            campaign_opt_in=_bool_or_none(data.get("campaign_opt_in"), field_name="campaign_opt_in"),
            events_opt_in=_bool_or_none(data.get("events_opt_in"), field_name="events_opt_in"),
            volunteer_opt_in=_bool_or_none(data.get("volunteer_opt_in"), field_name="volunteer_opt_in"),
            digest_frequency=str(data.get("digest_frequency") or "").strip().lower() or None,
            source="staff_console",
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    payload["donor"] = {"id": int(donor.id), "name": str(donor.name or "")}
    return jsonify(payload), 200


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
