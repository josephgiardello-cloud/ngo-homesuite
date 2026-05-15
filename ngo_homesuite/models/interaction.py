from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from ..auth.session import CURRENT_USER, require_role
from ..db.connection import run_db
from ..db.utils import audit, print_table
from ..prompts import (
    prompt_int,
    prompt_non_empty,
    prompt_optional,
    utc_now_iso,
    utc_today_iso,
)


def _donor_exists(cur: Any, donor_id: int) -> bool:
    cur.execute("SELECT 1 FROM donors WHERE id = ?", (donor_id,))
    return cur.fetchone() is not None


@require_role("admin", "fundraiser")
def log_donor_interaction() -> None:
    donor_id = prompt_int("Donor ID: ")

    def exists_op(_conn: Any, cur: Any) -> bool:
        return _donor_exists(cur, donor_id)

    if not run_db(exists_op):
        print("Donor not found.")
        return

    allowed = {"call", "email", "meeting", "sms", "mail", "other"}
    channel_raw = prompt_optional("Channel (call/email/meeting/sms/mail/other) [call]: ").strip().lower()
    channel = channel_raw or "call"
    if channel not in allowed:
        print("Invalid channel.")
        return

    summary = prompt_non_empty("Summary: ")
    if len(summary) > 500:
        print("Summary is too long (max 500 chars).")
        return

    next_action = prompt_optional("Next action (optional): ").strip() or None
    if next_action is not None and len(next_action) > 250:
        print("Next action is too long (max 250 chars).")
        return

    due_raw = prompt_optional("Follow-up due date (YYYY-MM-DD, optional): ").strip()
    follow_up_due: str | None = None
    if due_raw:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", due_raw) is None:
            print("Please use YYYY-MM-DD.")
            return
        try:
            datetime.fromisoformat(due_raw)
        except ValueError:
            print("Invalid date.")
            return
        follow_up_due = due_raw

    def op(conn: Any, cur: Any) -> int:
        cur.execute(
            "INSERT INTO donor_interactions "
            "(donor_id, occurred_at, channel, summary, next_action, follow_up_due, completed_at, created_at, created_by_user_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                donor_id,
                utc_now_iso(),
                channel,
                summary,
                next_action,
                follow_up_due,
                None,
                utc_now_iso(),
                int(CURRENT_USER["id"]) if CURRENT_USER and CURRENT_USER.get("id") is not None else None,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)

    iid = run_db(op, write=True)
    print(f"Interaction logged. (ID: {iid})")
    audit(
        "donor.interaction.create",
        entity_type="donor_interaction",
        entity_id=int(iid),
        details={"donor_id": int(donor_id), "channel": channel, "follow_up_due": follow_up_due},
    )


@require_role("admin", "fundraiser", "viewer")
def view_donor_timeline() -> None:
    donor_id = prompt_int("Donor ID: ")

    def donor_op(_conn: Any, cur: Any):
        cur.execute("SELECT id, name, email, phone FROM donors WHERE id = ?", (donor_id,))
        donor = cur.fetchone()
        cur.execute("SELECT tag FROM donor_tags WHERE donor_id = ? ORDER BY tag ASC", (donor_id,))
        tags = [r[0] for r in (cur.fetchall() or [])]
        return donor, tags

    donor, tags = run_db(donor_op)
    if not donor:
        print("Donor not found.")
        return

    print_table(["ID", "Name", "Email", "Phone"], [donor])
    print(f"Tags: {', '.join(tags) if tags else '(none)'}")

    def op(_conn: Any, cur: Any):
        cur.execute(
            "SELECT id, occurred_at, channel, follow_up_due, completed_at, summary "
            "FROM donor_interactions "
            "WHERE donor_id = ? "
            "ORDER BY occurred_at DESC, id DESC "
            "LIMIT 25",
            (donor_id,),
        )
        return cur.fetchall() or []

    rows = run_db(op)
    if not rows:
        print("No interactions logged for this donor yet.")
        return
    print_table(["Interaction ID", "Occurred", "Channel", "Follow-up Due", "Completed", "Summary"], rows)


@require_role("admin", "fundraiser")
def followups_due() -> None:
    today = utc_today_iso()

    def op(_conn: Any, cur: Any):
        cur.execute(
            "SELECT di.id, di.follow_up_due, donors.id, donors.name, di.channel, di.summary "
            "FROM donor_interactions di JOIN donors ON donors.id = di.donor_id "
            "WHERE di.follow_up_due IS NOT NULL "
            "AND di.completed_at IS NULL "
            "AND di.follow_up_due <= ? "
            "ORDER BY di.follow_up_due ASC, di.id ASC "
            "LIMIT 50",
            (today,),
        )
        return cur.fetchall() or []

    rows = run_db(op)
    if not rows:
        print("No follow-ups due.")
        return
    print_table(["Interaction ID", "Due", "Donor ID", "Donor", "Channel", "Summary"], rows)


@require_role("admin", "fundraiser")
def complete_followup() -> None:
    interaction_id = prompt_int("Interaction ID to mark completed: ")
    confirm = prompt_optional("Mark completed? (y/N): ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        return

    def op(conn: Any, cur: Any) -> int:
        cur.execute(
            "UPDATE donor_interactions SET completed_at = ? "
            "WHERE id = ? AND completed_at IS NULL",
            (utc_now_iso(), interaction_id),
        )
        conn.commit()
        return int(cur.rowcount or 0)

    updated = run_db(op, write=True)
    if updated <= 0:
        print("Not found, or already completed.")
        return

    print("Follow-up marked completed.")
    audit(
        "donor.interaction.complete",
        entity_type="donor_interaction",
        entity_id=int(interaction_id),
    )
