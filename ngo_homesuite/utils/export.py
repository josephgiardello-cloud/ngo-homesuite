from __future__ import annotations

import csv
import sqlite3
from pathlib import Path
from typing import Any, cast

from ..auth.session import CURRENT_USER, require_role
from ..config import DEFAULT_EXPORT_DIR
from ..db.connection import run_db
from ..db.utils import audit
from ..prompts import prompt_optional, utc_now_compact


def _export_query_to_csv(cur: Any, sql: str, params: tuple[Any, ...], out_path: Path) -> None:
    cur.execute(sql, params)
    rows = cast(list[tuple[Any, ...]], cur.fetchall() or [])
    description = cast(list[tuple[Any, ...]], cur.description or [])
    headers: list[str] = [str(d[0]) for d in description]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)


def _table_has_column(cur: Any, table_name: str, column_name: str) -> bool:
    cur.execute(f"PRAGMA table_info({table_name})")
    rows = cast(list[tuple[Any, ...]], cur.fetchall() or [])
    return any(str(row[1]) == column_name for row in rows if len(row) > 1)


def _resolve_current_user_org_id(cur: Any) -> int | None:
    if not CURRENT_USER:
        return None

    direct = CURRENT_USER.get("organization_id")
    if direct is not None:
        return int(direct)

    user_id = CURRENT_USER.get("id")
    if user_id is None:
        return None

    if not _table_has_column(cur, "users", "organization_id"):
        return None

    cur.execute("SELECT organization_id FROM users WHERE id = ?", (int(user_id),))
    row = cur.fetchone()
    if not row or row[0] is None:
        return None
    return int(row[0])


def _build_export_query(
    cur: Any,
    *,
    table: str,
    columns_sql: str,
    organization_id: int | None,
    tenant_mode: bool,
) -> tuple[str, tuple[Any, ...]]:
    if not tenant_mode:
        return f"SELECT {columns_sql} FROM {table} ORDER BY id ASC", ()

    if organization_id is None:
        raise ValueError("Tenant-scoped export requires organization_id on the current user.")

    if _table_has_column(cur, table, "organization_id"):
        return (
            f"SELECT {columns_sql} FROM {table} WHERE organization_id = ? ORDER BY id ASC",
            (organization_id,),
        )

    if table == "donation_allocations" and _table_has_column(cur, "donations", "organization_id"):
        return (
            "SELECT donation_allocations.id, donation_allocations.donation_id, donation_allocations.project_id, "
            "donation_allocations.category, donation_allocations.amount "
            "FROM donation_allocations "
            "JOIN donations ON donations.id = donation_allocations.donation_id "
            "WHERE donations.organization_id = ? "
            "ORDER BY donation_allocations.id ASC",
            (organization_id,),
        )

    if table == "payroll_items" and _table_has_column(cur, "payroll_runs", "organization_id"):
        return (
            "SELECT payroll_items.id, payroll_items.payroll_run_id, payroll_items.staff_id, payroll_items.jurisdiction_code, "
            "payroll_items.hours_regular, payroll_items.hours_overtime, payroll_items.gross, payroll_items.tax_withheld, "
            "payroll_items.other_withheld, payroll_items.net, payroll_items.employer_tax, payroll_items.employer_other, "
            "payroll_items.employer_total, payroll_items.total_cost "
            "FROM payroll_items "
            "JOIN payroll_runs ON payroll_runs.id = payroll_items.payroll_run_id "
            "WHERE payroll_runs.organization_id = ? "
            "ORDER BY payroll_items.id ASC",
            (organization_id,),
        )

    raise ValueError(
        f"Refusing unscoped export for table '{table}' in tenant mode. Add an organization-aware query first."
    )


def _export_table(
    cur: Any,
    *,
    table: str,
    columns_sql: str,
    out_path: Path,
    organization_id: int | None,
    tenant_mode: bool,
) -> None:
    sql, params = _build_export_query(
        cur,
        table=table,
        columns_sql=columns_sql,
        organization_id=organization_id,
        tenant_mode=tenant_mode,
    )
    _export_query_to_csv(cur, sql, params, out_path)


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
        tenant_mode = _table_has_column(cur, "donors", "organization_id")
        organization_id = _resolve_current_user_org_id(cur)

        # Allowed for admin + fundraiser
        _export_table(cur, table="donors", columns_sql="id, name, email, phone, address", out_path=out_dir / "donors.csv", organization_id=organization_id, tenant_mode=tenant_mode)
        _export_table(cur, table="campaigns", columns_sql="id, name, start_date, end_date, goal_amount, active", out_path=out_dir / "campaigns.csv", organization_id=organization_id, tenant_mode=tenant_mode)
        _export_table(cur, table="pledges", columns_sql="id, donor_id, campaign_id, amount, frequency, start_date, end_date, active", out_path=out_dir / "pledges.csv", organization_id=organization_id, tenant_mode=tenant_mode)
        _export_table(cur, table="donations", columns_sql="id, donor_id, campaign_id, pledge_id, amount, date, purpose", out_path=out_dir / "donations.csv", organization_id=organization_id, tenant_mode=tenant_mode)
        _export_table(cur, table="donation_allocations", columns_sql="id, donation_id, project_id, category, amount", out_path=out_dir / "donation_allocations.csv", organization_id=organization_id, tenant_mode=tenant_mode)
        _export_table(cur, table="projects", columns_sql="id, name, description, budget, spent, start_date", out_path=out_dir / "projects.csv", organization_id=organization_id, tenant_mode=tenant_mode)

        # Admin-only exports
        if is_admin:
            _export_table(
                cur,
                table="campaigns",
                columns_sql=(
                    "id, name, start_date, end_date, goal_amount, active, notes, created_at, created_by_user_id"
                ),
                out_path=out_dir / "campaigns_admin.csv",
                organization_id=organization_id,
                tenant_mode=tenant_mode,
            )
            _export_table(
                cur,
                table="pledges",
                columns_sql=(
                    "id, donor_id, campaign_id, amount, frequency, start_date, end_date, active, notes, created_at, created_by_user_id"
                ),
                out_path=out_dir / "pledges_admin.csv",
                organization_id=organization_id,
                tenant_mode=tenant_mode,
            )
            _export_table(
                cur,
                table="donor_tags",
                columns_sql="id, donor_id, tag, created_at, created_by_user_id",
                out_path=out_dir / "donor_tags.csv",
                organization_id=organization_id,
                tenant_mode=tenant_mode,
            )
            _export_table(
                cur,
                table="donor_interactions",
                columns_sql=(
                    "id, donor_id, occurred_at, channel, summary, next_action, follow_up_due, completed_at, created_at, created_by_user_id"
                ),
                out_path=out_dir / "donor_interactions.csv",
                organization_id=organization_id,
                tenant_mode=tenant_mode,
            )
            _export_table(
                cur,
                table="volunteers",
                columns_sql="id, name, email, phone, skills, availability",
                out_path=out_dir / "volunteers.csv",
                organization_id=organization_id,
                tenant_mode=tenant_mode,
            )
            _export_table(
                cur,
                table="staff",
                columns_sql=(
                    "id, name, title, jurisdiction_code, pay_type, pay_frequency, hourly_rate, annual_salary, "
                    "overtime_multiplier, tax_withholding_pct, other_withholding_pct, other_withholding_fixed, "
                    "employer_tax_pct, employer_other_pct, employer_other_fixed, active, start_date, end_date, notes"
                ),
                out_path=out_dir / "staff.csv",
                organization_id=organization_id,
                tenant_mode=tenant_mode,
            )
            _export_table(
                cur,
                table="payroll_runs",
                columns_sql="id, period_start, period_end, run_at, COALESCE(status, 'paid') AS status, paid_at, created_by_user_id",
                out_path=out_dir / "payroll_runs.csv",
                organization_id=organization_id,
                tenant_mode=tenant_mode,
            )
            _export_table(
                cur,
                table="payroll_items",
                columns_sql=(
                    "id, payroll_run_id, staff_id, jurisdiction_code, hours_regular, hours_overtime, gross, "
                    "tax_withheld, other_withheld, net, employer_tax, employer_other, employer_total, total_cost"
                ),
                out_path=out_dir / "payroll_items.csv",
                organization_id=organization_id,
                tenant_mode=tenant_mode,
            )
            _export_table(
                cur,
                table="users",
                columns_sql="id, username, role, created_at",
                out_path=out_dir / "users.csv",
                organization_id=organization_id,
                tenant_mode=tenant_mode,
            )

    try:
        run_db(op)
    except (sqlite3.DatabaseError, ValueError) as e:
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
