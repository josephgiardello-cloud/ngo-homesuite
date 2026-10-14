from __future__ import annotations

import csv
import sqlite3
from pathlib import Path
from typing import Any

from ..auth.session import CURRENT_USER, require_role
from ..config import DEFAULT_EXPORT_DIR
from ..db.connection import run_db
from ..db.utils import audit
from ..prompts import prompt_optional, utc_now_compact


def _export_query_to_csv(cur: Any, sql: str, params: tuple[Any, ...], out_path: Path) -> None:
    cur.execute(sql, params)
    rows = cur.fetchall() or []
    headers = [d[0] for d in (cur.description or [])]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)


@require_role("admin", "fundraiser")
def export_data_csv() -> None:
    """Export key tables to CSV files.

    Admin exports include all operational tables (excluding password fields).
    Fundraiser exports are intentionally limited to non-sensitive tables.
    """

    default_dir = Path(DEFAULT_EXPORT_DIR) / f"export_{utc_now_compact()}"
    raw = prompt_optional(f"Export folder (blank for {default_dir}): ").strip()
    out_dir = Path(raw) if raw else default_dir

    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"Could not create export folder: {e}")
        return

    def op(_conn: Any, cur: Any) -> None:
        role = str(CURRENT_USER.get("role")) if CURRENT_USER else ""
        is_admin = role == "admin"

        # Allowed for admin + fundraiser
        _export_query_to_csv(cur, "SELECT id, name, email, phone, address FROM donors ORDER BY id ASC", (), out_dir / "donors.csv")
        _export_query_to_csv(cur, "SELECT id, name, start_date, end_date, goal_amount, active FROM campaigns ORDER BY id ASC", (), out_dir / "campaigns.csv")
        _export_query_to_csv(cur, "SELECT id, donor_id, campaign_id, amount, frequency, start_date, end_date, active FROM pledges ORDER BY id ASC", (), out_dir / "pledges.csv")
        _export_query_to_csv(cur, "SELECT id, donor_id, campaign_id, pledge_id, amount, date, purpose FROM donations ORDER BY id ASC", (), out_dir / "donations.csv")
        _export_query_to_csv(cur, "SELECT id, donation_id, project_id, category, amount FROM donation_allocations ORDER BY id ASC", (), out_dir / "donation_allocations.csv")
        _export_query_to_csv(cur, "SELECT id, name, description, budget, spent, start_date FROM projects ORDER BY id ASC", (), out_dir / "projects.csv")

        # Admin-only exports
        if is_admin:
            _export_query_to_csv(
                cur,
                "SELECT id, name, start_date, end_date, goal_amount, active, notes, created_at, created_by_user_id "
                "FROM campaigns ORDER BY id ASC",
                (),
                out_dir / "campaigns_admin.csv",
            )
            _export_query_to_csv(
                cur,
                "SELECT id, donor_id, campaign_id, amount, frequency, start_date, end_date, active, notes, created_at, created_by_user_id "
                "FROM pledges ORDER BY id ASC",
                (),
                out_dir / "pledges_admin.csv",
            )
            _export_query_to_csv(
                cur,
                "SELECT id, donor_id, tag, created_at, created_by_user_id FROM donor_tags ORDER BY id ASC",
                (),
                out_dir / "donor_tags.csv",
            )
            _export_query_to_csv(
                cur,
                "SELECT id, donor_id, occurred_at, channel, summary, next_action, follow_up_due, completed_at, created_at, created_by_user_id "
                "FROM donor_interactions ORDER BY id ASC",
                (),
                out_dir / "donor_interactions.csv",
            )
            _export_query_to_csv(cur, "SELECT id, name, email, phone, skills, availability FROM volunteers ORDER BY id ASC", (), out_dir / "volunteers.csv")
            _export_query_to_csv(
                cur,
                "SELECT id, name, title, jurisdiction_code, pay_type, pay_frequency, hourly_rate, annual_salary, "
                "overtime_multiplier, tax_withholding_pct, other_withholding_pct, other_withholding_fixed, "
                "employer_tax_pct, employer_other_pct, employer_other_fixed, active, start_date, end_date, notes "
                "FROM staff ORDER BY id ASC",
                (),
                out_dir / "staff.csv",
            )
            _export_query_to_csv(
                cur,
                "SELECT id, period_start, period_end, run_at, COALESCE(status, 'paid') AS status, paid_at, created_by_user_id "
                "FROM payroll_runs ORDER BY id ASC",
                (),
                out_dir / "payroll_runs.csv",
            )
            _export_query_to_csv(
                cur,
                "SELECT id, payroll_run_id, staff_id, jurisdiction_code, hours_regular, hours_overtime, gross, "
                "tax_withheld, other_withheld, net, employer_tax, employer_other, employer_total, total_cost "
                "FROM payroll_items ORDER BY id ASC",
                (),
                out_dir / "payroll_items.csv",
            )
            _export_query_to_csv(cur, "SELECT id, username, role, created_at FROM users ORDER BY id ASC", (), out_dir / "users.csv")

    try:
        run_db(op)
    except sqlite3.DatabaseError as e:
        print(f"Export failed: {e}")
        return

    role = str(CURRENT_USER.get("role")) if CURRENT_USER else ""
    if role == "admin":
        print(f"Export complete: {out_dir}")
    else:
        print(f"Export complete (restricted): {out_dir}")

    audit(
        "data.export.csv",
        entity_type="export",
        details={"out_dir": str(out_dir), "restricted": (role != "admin")},
    )
