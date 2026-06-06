from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from flask import current_app, jsonify, request
from flask_login import current_user
from sqlalchemy import func, select

from ngo_homesuite.models.core import CollaborationChannel, CollaborationChannelMember, CollaborationMessage, CollaborationPresence, User, db


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


def _typing_store() -> dict[tuple[int, int, int], datetime]:
    store = current_app.extensions.get("collab_typing_store")
    if store is None:
        store = {}
        current_app.extensions["collab_typing_store"] = store
    return store


def _typing_ttl_seconds() -> int:
    return 20


def _moderation_store() -> dict[int, dict[str, Any]]:
    store = current_app.extensions.get("collab_moderation_store")
    if store is None:
        store = {}
        current_app.extensions["collab_moderation_store"] = store
    return store


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


def list_collaboration_channels():
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


def create_collaboration_channel():
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


def list_collaboration_messages(channel_id: int):
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


def create_collaboration_message(channel_id: int):
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


def upsert_collaboration_presence():
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


def list_collaboration_presence():
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


def upsert_collaboration_typing(channel_id: int):
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

    data = request.get_json(silent=True) or {}
    is_typing = bool(data.get("is_typing", True))
    key = (int(org_id), int(channel_id), int(current_user_id))
    store = _typing_store()
    if is_typing:
        store[key] = _utcnow_naive()
    else:
        store.pop(key, None)
    return jsonify({"channel_id": int(channel_id), "user_id": current_user_id, "is_typing": is_typing}), 200


def list_collaboration_typing(channel_id: int):
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

    now = _utcnow_naive()
    ttl_seconds = _typing_ttl_seconds()
    store = _typing_store()

    stale_keys = [k for k, started_at in store.items() if (now - started_at).total_seconds() > ttl_seconds]
    for stale in stale_keys:
        store.pop(stale, None)

    user_ids = [
        int(user_id)
        for (store_org_id, store_channel_id, user_id), _started_at in store.items()
        if int(store_org_id) == int(org_id)
        and int(store_channel_id) == int(channel_id)
        and int(user_id) != int(current_user_id)
    ]

    return jsonify({"channel_id": int(channel_id), "count": len(user_ids), "typing_user_ids": sorted(user_ids)}), 200


def moderate_collaboration_channel(channel_id: int):
    org_id = _org_id()
    current_user_id = int(getattr(current_user, "id", 0) or 0)
    channel = db.session.scalar(
        select(CollaborationChannel).where(
            CollaborationChannel.organization_id == org_id,
            CollaborationChannel.id == int(channel_id),
        ).limit(1)
    )
    if channel is None:
        return jsonify({"error": "channel not found"}), 404

    actor_member = db.session.scalar(
        select(CollaborationChannelMember).where(
            CollaborationChannelMember.organization_id == org_id,
            CollaborationChannelMember.channel_id == int(channel_id),
            CollaborationChannelMember.user_id == current_user_id,
            CollaborationChannelMember.is_active.is_(True),
        ).limit(1)
    )
    if actor_member is None:
        return jsonify({"error": "channel not found"}), 404

    data = _json_or_400(required=["action"])
    action = str(data.get("action") or "").strip().lower()
    if action in {"archive", "unarchive"}:
        can_moderate = str(actor_member.role or "").lower() == "owner" or int(channel.created_by_user_id or 0) == current_user_id
        if not can_moderate:
            return jsonify({"error": "insufficient permissions for moderation"}), 403
        channel.is_archived = bool(action == "archive")
        channel.updated_at = _utcnow_naive()
        db.session.commit()
        return jsonify({"channel_id": int(channel.id), "is_archived": bool(channel.is_archived), "action": action}), 200

    if action in {"mute_member", "unmute_member"}:
        target_user_id = data.get("target_user_id")
        try:
            target_user_id_int = int(target_user_id)
        except (TypeError, ValueError):
            return jsonify({"error": "target_user_id must be an integer"}), 400
        moderation = _moderation_store().setdefault(int(channel_id), {"muted_user_ids": set()})
        muted_user_ids = moderation.setdefault("muted_user_ids", set())
        if action == "mute_member":
            muted_user_ids.add(int(target_user_id_int))
        else:
            muted_user_ids.discard(int(target_user_id_int))
        return jsonify({"channel_id": int(channel_id), "action": action, "muted_user_ids": sorted(int(x) for x in muted_user_ids)}), 200

    return jsonify({"error": "action must be one of: archive, unarchive, mute_member, unmute_member"}), 400


def collaboration_inbox_summary():
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
        )
    )
    channel_ids = [int(item.channel_id) for item in memberships]
    if not channel_ids:
        return jsonify({"count": 0, "items": [], "summary": {"sla_breached": 0, "high_priority": 0}}), 200

    channels = list(
        db.session.scalars(
            select(CollaborationChannel)
            .where(
                CollaborationChannel.organization_id == org_id,
                CollaborationChannel.id.in_(channel_ids),
                CollaborationChannel.is_archived.is_(False),
            )
        )
    )

    now = _utcnow_naive()
    items: list[dict[str, Any]] = []
    sla_breached = 0
    high_priority = 0
    for channel in channels:
        latest = db.session.scalar(
            select(CollaborationMessage)
            .where(
                CollaborationMessage.organization_id == org_id,
                CollaborationMessage.channel_id == int(channel.id),
            )
            .order_by(CollaborationMessage.created_at.desc(), CollaborationMessage.id.desc())
            .limit(1)
        )
        unread = int(
            db.session.scalar(
                select(func.count(CollaborationMessage.id)).where(
                    CollaborationMessage.organization_id == org_id,
                    CollaborationMessage.channel_id == int(channel.id),
                    CollaborationMessage.sender_user_id != current_user_id,
                    CollaborationMessage.created_at > (next((m.last_read_at for m in memberships if int(m.channel_id) == int(channel.id)), None) or datetime(1970, 1, 1)),
                )
            )
            or 0
        )
        latest_age_minutes = None
        if latest is not None and latest.created_at is not None:
            latest_age_minutes = max(0, int((now - latest.created_at).total_seconds() // 60))
        sla_status = "healthy"
        if unread > 0 and latest_age_minutes is not None and latest_age_minutes >= 180:
            sla_status = "breached"
            sla_breached += 1
        elif unread > 0 and latest_age_minutes is not None and latest_age_minutes >= 60:
            sla_status = "warning"
            high_priority += 1
        items.append(
            {
                "channel_id": int(channel.id),
                "channel_type": str(channel.channel_type or "team"),
                "name": channel.name,
                "unread_count": unread,
                "latest_message_at": latest.created_at.isoformat() if latest is not None and latest.created_at else None,
                "latest_message_age_minutes": latest_age_minutes,
                "sla_status": sla_status,
            }
        )

    items.sort(key=lambda row: (str(row.get("sla_status") != "breached"), -int(row.get("unread_count") or 0)))
    return jsonify({"count": len(items), "items": items, "summary": {"sla_breached": sla_breached, "high_priority": high_priority}}), 200
