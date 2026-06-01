from __future__ import annotations

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import text

from ngo_homesuite.events.services import _ensure_email_tables, _ensure_event_tables, send_event_reminder
from ngo_homesuite.models.core import db
from ngo_homesuite.web.rbac import roles_required


events_bp = Blueprint("events", __name__)


def _ensure_event_storage() -> None:
    _ensure_event_tables()
    _ensure_email_tables()


def _events_query(limit: int = 20) -> list[dict]:
    _ensure_event_storage()
    rows = db.session.execute(
        text(
            """
            SELECT id, name, description, start_date, end_date, created_at, updated_at
            FROM events
            WHERE deleted_at IS NULL
            ORDER BY COALESCE(start_date, end_date, created_at) DESC, id DESC
            LIMIT :limit
            """
        ),
        {"limit": max(1, min(int(limit), 100))},
    ).mappings().all()

    events: list[dict] = []
    for row in rows:
        event_id = int(row["id"])
        registration_count = db.session.execute(
            text("SELECT COUNT(*) AS count FROM registrations WHERE deleted_at IS NULL AND event_id = :event_id"),
            {"event_id": event_id},
        ).mappings().first()
        queue_rows = db.session.execute(
            text(
                """
                SELECT status, COUNT(*) AS count
                FROM event_email_queue
                WHERE event_id = :event_id
                GROUP BY status
                """
            ),
            {"event_id": event_id},
        ).mappings().all()
        queue_counts = {"pending": 0, "retrying": 0, "sent": 0, "failed": 0, "suppressed": 0}
        for queue_row in queue_rows:
            queue_counts[str(queue_row["status"] or "pending")] = int(queue_row["count"])

        events.append(
            {
                "id": event_id,
                "name": str(row["name"] or "Event"),
                "description": row.get("description"),
                "start_date": row.get("start_date"),
                "end_date": row.get("end_date"),
                "registration_count": int((registration_count or {}).get("count") or 0),
                "queue_counts": queue_counts,
            }
        )
    return events


def _recent_registrations(limit: int = 10) -> list[dict]:
    _ensure_event_storage()
    rows = db.session.execute(
        text(
            """
            SELECT r.id, r.event_id, r.registered_at, d.name AS donor_name, d.email AS donor_email, e.name AS event_name
            FROM registrations r
            LEFT JOIN donors d ON d.id = r.donor_id
            LEFT JOIN events e ON e.id = r.event_id
            WHERE r.deleted_at IS NULL
            ORDER BY r.registered_at DESC, r.id DESC
            LIMIT :limit
            """
        ),
        {"limit": max(1, min(int(limit), 50))},
    ).mappings().all()
    return [
        {
            "id": int(row["id"]),
            "event_id": int(row["event_id"]),
            "event_name": row.get("event_name") or "Event",
            "registered_at": row.get("registered_at"),
            "donor_name": row.get("donor_name") or "Attendee",
            "donor_email": row.get("donor_email"),
        }
        for row in rows
    ]


@events_bp.get("/events")
@login_required
@roles_required("admin", "staff")
def events_board() -> str:
    _ensure_event_storage()
    return render_template(
        "events/board.html",
        active_page="events_board",
        events=_events_query(),
        recent_registrations=_recent_registrations(),
    )


@events_bp.post("/events/create")
@login_required
@roles_required("admin", "staff")
def create_event_route():
    _ensure_event_storage()
    name = (request.form.get("name") or "").strip()
    start_date = (request.form.get("start_date") or "").strip() or None
    end_date = (request.form.get("end_date") or "").strip() or None
    description = (request.form.get("description") or "").strip() or None

    if not name:
        flash("Event name is required.", "error")
        return redirect(url_for("events.events_board"))

    db.session.execute(
        text(
            """
            INSERT INTO events(name, description, start_date, end_date)
            VALUES (:name, :description, :start_date, :end_date)
            """
        ),
        {"name": name, "description": description, "start_date": start_date, "end_date": end_date},
    )
    db.session.commit()
    flash("Event created.", "success")
    return redirect(url_for("events.events_board"))


@events_bp.post("/events/<int:event_id>/update")
@login_required
@roles_required("admin", "staff")
def update_event_route(event_id: int):
    _ensure_event_storage()
    name = (request.form.get("name") or "").strip()
    start_date = (request.form.get("start_date") or "").strip() or None
    end_date = (request.form.get("end_date") or "").strip() or None
    description = (request.form.get("description") or "").strip() or None

    if not name:
        flash("Event name is required.", "error")
        return redirect(url_for("events.events_board"))

    result = db.session.execute(
        text(
            """
            UPDATE events
            SET name = :name,
                description = :description,
                start_date = :start_date,
                end_date = :end_date,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
            WHERE id = :event_id AND deleted_at IS NULL
            """
        ),
        {"event_id": int(event_id), "name": name, "description": description, "start_date": start_date, "end_date": end_date},
    )
    db.session.commit()
    if result.rowcount:
        flash("Event updated.", "success")
    else:
        flash("Event not found.", "error")
    return redirect(url_for("events.events_board"))


@events_bp.post("/events/<int:event_id>/send-reminders")
@login_required
@roles_required("admin", "staff")
def send_event_reminders_route(event_id: int):
    _ensure_event_storage()
    rows = db.session.execute(
        text(
            """
            SELECT DISTINCT d.email AS email, COALESCE(d.name, 'Attendee') AS name
            FROM registrations r
            JOIN donors d ON d.id = r.donor_id
            WHERE r.event_id = :event_id
              AND r.deleted_at IS NULL
              AND d.email IS NOT NULL
              AND d.email != ''
            """
        ),
        {"event_id": int(event_id)},
    ).mappings().all()

    sent = 0
    failed = 0
    for row in rows:
        if send_event_reminder(int(event_id), str(row["email"]), str(row["name"])):
            sent += 1
        else:
            failed += 1

    flash(f"Sent reminders: {sent}. Failed: {failed}.", "success" if sent else "error")
    return redirect(url_for("events.events_board"))


@events_bp.get("/api/events")
@login_required
@roles_required("admin", "staff")
def api_list_events():
    _ensure_event_storage()
    return jsonify(_events_query())


@events_bp.post("/api/events")
@login_required
@roles_required("admin", "staff")
def api_create_event():
    _ensure_event_storage()
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400

    db.session.execute(
        text(
            """
            INSERT INTO events(name, description, start_date, end_date)
            VALUES (:name, :description, :start_date, :end_date)
            """
        ),
        {
            "name": name,
            "description": (str(payload.get("description") or "").strip() or None),
            "start_date": (str(payload.get("start_date") or "").strip() or None),
            "end_date": (str(payload.get("end_date") or "").strip() or None),
        },
    )
    db.session.commit()
    return jsonify({"ok": True, "events": _events_query(limit=1)}), 201


@events_bp.get("/api/events/<int:event_id>/registrations")
@login_required
@roles_required("admin", "staff")
def api_event_registrations(event_id: int):
    _ensure_event_storage()
    rows = db.session.execute(
        text(
            """
            SELECT r.id, r.registered_at, d.name AS donor_name, d.email AS donor_email
            FROM registrations r
            LEFT JOIN donors d ON d.id = r.donor_id
            WHERE r.deleted_at IS NULL AND r.event_id = :event_id
            ORDER BY r.registered_at DESC, r.id DESC
            """
        ),
        {"event_id": int(event_id)},
    ).mappings().all()
    return jsonify(
        {
            "event_id": int(event_id),
            "count": len(rows),
            "registrations": [
                {
                    "id": int(row["id"]),
                    "registered_at": row.get("registered_at"),
                    "donor_name": row.get("donor_name") or "Attendee",
                    "donor_email": row.get("donor_email"),
                }
                for row in rows
            ],
        }
    )


@events_bp.post("/api/events/<int:event_id>/send-reminders")
@login_required
@roles_required("admin", "staff")
def api_send_event_reminders(event_id: int):
    _ensure_event_storage()
    rows = db.session.execute(
        text(
            """
            SELECT DISTINCT d.email AS email, COALESCE(d.name, 'Attendee') AS name
            FROM registrations r
            JOIN donors d ON d.id = r.donor_id
            WHERE r.event_id = :event_id
              AND r.deleted_at IS NULL
              AND d.email IS NOT NULL
              AND d.email != ''
            """
        ),
        {"event_id": int(event_id)},
    ).mappings().all()

    sent = 0
    failed = 0
    for row in rows:
        if send_event_reminder(int(event_id), str(row["email"]), str(row["name"])):
            sent += 1
        else:
            failed += 1
    return jsonify({"event_id": int(event_id), "sent": sent, "failed": failed, "total": len(rows)})