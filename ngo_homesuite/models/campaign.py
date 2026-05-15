from __future__ import annotations

import sqlite3
from typing import Any

from ..auth.session import CURRENT_USER, require_role
from ..db.connection import run_db
from ..db.utils import audit, print_table
from ..prompts import (
    prompt_date_iso,
    prompt_non_empty,
    prompt_optional,
    utc_now_iso,
)


@require_role("admin", "fundraiser")
def add_campaign() -> None:
    name = prompt_non_empty("Campaign name: ").strip()
    if len(name) > 120:
        print("Campaign name too long (max 120 chars).")
        return

    start_date_raw = prompt_optional("Start date (YYYY-MM-DD, optional): ").strip()
    start_date: str | None = None
    if start_date_raw:
        start_date = prompt_date_iso("Start date (YYYY-MM-DD): ")

    end_date_raw = prompt_optional("End date (YYYY-MM-DD, optional): ").strip()
    end_date: str | None = None
    if end_date_raw:
        end_date = prompt_date_iso("End date (YYYY-MM-DD): ")

    goal_amount_raw = prompt_optional("Goal amount (optional): ").strip()
    goal_amount: float | None = None
    if goal_amount_raw:
        try:
            goal_amount = float(goal_amount_raw)
        except ValueError:
            print("Invalid goal amount.")
            return

    active_raw = prompt_optional("Active? (Y/n): ").strip().lower()
    active = 0 if active_raw == "n" else 1

    notes = prompt_optional("Notes (optional): ")
    if notes and len(notes) > 2000:
        print("Notes too long (max 2000 chars).")
        return

    def op(conn: Any, cur: Any) -> int:
        cur.execute(
            "INSERT INTO campaigns (name, start_date, end_date, goal_amount, active, notes, created_at, created_by_user_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                name,
                start_date,
                end_date,
                goal_amount,
                active,
                notes,
                utc_now_iso(),
                int(CURRENT_USER["id"]) if CURRENT_USER and CURRENT_USER.get("id") is not None else None,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)

    try:
        cid = run_db(op, write=True)
    except sqlite3.IntegrityError as e:
        print(f"Could not add campaign: {e}")
        return

    print(f"Campaign added. (ID: {cid})")
    audit("campaign.create", entity_type="campaign", entity_id=int(cid), details={"name": name})


@require_role("admin", "fundraiser", "viewer")
def view_campaigns() -> None:
    def op(_conn: Any, cur: Any):
        cur.execute(
            "SELECT id, name, COALESCE(start_date, ''), COALESCE(end_date, ''), COALESCE(goal_amount, 0), active "
            "FROM campaigns ORDER BY id DESC"
        )
        return cur.fetchall() or []

    rows = run_db(op)
    if not rows:
        print("No campaigns yet.")
        return

    formatted: list[tuple[Any, ...]] = []
    for cid, name, start, end, goal, active in rows:
        try:
            goal_str = f"${float(goal or 0):.2f}" if goal is not None else ""
        except (TypeError, ValueError):
            goal_str = str(goal)
        formatted.append((cid, name, start, end, goal_str, "yes" if int(active or 0) == 1 else "no"))

    print_table(["ID", "Name", "Start", "End", "Goal", "Active"], formatted)


@require_role("admin", "fundraiser", "viewer")
def campaign_report() -> None:
    def op(_conn: Any, cur: Any):
        cur.execute(
            "SELECT c.id, c.name, c.active, c.start_date, c.end_date, c.goal_amount, "
            "COALESCE(COUNT(d.id), 0) AS gifts, COALESCE(SUM(d.amount), 0) AS total, "
            "COALESCE(AVG(d.amount), 0) AS avg_gift, MIN(d.date) AS first_gift, MAX(d.date) AS last_gift "
            "FROM campaigns c "
            "LEFT JOIN donations d ON d.campaign_id = c.id "
            "GROUP BY c.id, c.name, c.active, c.start_date, c.end_date, c.goal_amount "
            "ORDER BY c.id DESC"
        )
        by_campaign = cur.fetchall() or []
        cur.execute("SELECT COALESCE(COUNT(*), 0), COALESCE(SUM(amount), 0) FROM donations WHERE campaign_id IS NULL")
        unattributed = cur.fetchone() or (0, 0)
        return by_campaign, unattributed

    by_campaign, unattributed = run_db(op)
    print("\nCampaign Report")

    try:
        un_count = int(unattributed[0] or 0)
    except (TypeError, ValueError):
        un_count = 0
    try:
        un_total = float(unattributed[1] or 0)
    except (TypeError, ValueError):
        un_total = 0.0

    print(f"Unattributed donations: {un_count} (${un_total:.2f})")

    if not by_campaign:
        print("No campaigns.")
        return

    rows: list[tuple[Any, ...]] = []
    for cid, name, active, start, end, goal, gifts, total, avg_gift, first_gift, last_gift in by_campaign:
        try:
            total_str = f"${float(total or 0):.2f}"
        except (TypeError, ValueError):
            total_str = str(total)
        try:
            avg_str = f"${float(avg_gift or 0):.2f}"
        except (TypeError, ValueError):
            avg_str = str(avg_gift)
        try:
            goal_str = f"${float(goal or 0):.2f}" if goal is not None else ""
        except (TypeError, ValueError):
            goal_str = str(goal)

        rows.append(
            (
                cid,
                name,
                "yes" if int(active or 0) == 1 else "no",
                start or "",
                end or "",
                goal_str,
                int(gifts or 0),
                total_str,
                avg_str,
                first_gift or "",
                last_gift or "",
            )
        )

    print_table(["ID", "Name", "Active", "Start", "End", "Goal", "Gifts", "Total", "Avg", "First", "Last"], rows)
