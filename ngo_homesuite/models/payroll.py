from __future__ import annotations

from typing import Any

from ..auth.session import CURRENT_USER, require_role
from ..db.connection import run_db
from ..db.utils import audit, print_table
from ..prompts import (
    prompt_date_iso,
    prompt_non_empty,
    prompt_non_negative_amount,
    prompt_optional,
    utc_now_iso,
)


def _pay_frequency_periods_per_year(freq: str) -> int:
    freq = (freq or "").strip().lower()
    if freq == "weekly":
        return 52
    if freq == "biweekly":
        return 26
    if freq == "monthly":
        return 12
    # Default conservative assumption
    return 26


@require_role("admin")
def run_payroll() -> None:
    period_start = prompt_date_iso("Pay period start (YYYY-MM-DD): ")
    period_end = prompt_date_iso("Pay period end (YYYY-MM-DD): ")

    def fetch_staff_op(_conn: Any, cur: Any):
        cur.execute(
            "SELECT id, name, title, COALESCE(NULLIF(TRIM(jurisdiction_code), ''), 'US-ME') AS jurisdiction_code, "
            "pay_type, pay_frequency, hourly_rate, annual_salary, "
            "COALESCE(overtime_multiplier, 1.5), COALESCE(tax_withholding_pct, 0), "
            "COALESCE(other_withholding_pct, 0), COALESCE(other_withholding_fixed, 0) "
            ", COALESCE(employer_tax_pct, 0), COALESCE(employer_other_pct, 0), COALESCE(employer_other_fixed, 0) "
            "FROM staff WHERE active = 1 ORDER BY id ASC"
        )
        staff_rows = cur.fetchall() or []

        # Funding check: allocated vs reserved vs spent.
        cur.execute("SELECT COALESCE(SUM(amount), 0) FROM donation_allocations WHERE category = 'payroll'")
        payroll_funding = float((cur.fetchone() or [0])[0] or 0)

        cur.execute(
            "SELECT COALESCE(SUM(COALESCE(pi.total_cost, pi.gross)), 0) "
            "FROM payroll_items pi JOIN payroll_runs pr ON pr.id = pi.payroll_run_id "
            "WHERE COALESCE(pr.status, 'paid') = 'committed'"
        )
        payroll_reserved = float((cur.fetchone() or [0])[0] or 0)

        cur.execute(
            "SELECT COALESCE(SUM(COALESCE(pi.total_cost, pi.gross)), 0) "
            "FROM payroll_items pi JOIN payroll_runs pr ON pr.id = pi.payroll_run_id "
            "WHERE COALESCE(pr.status, 'paid') = 'paid'"
        )
        payroll_spent = float((cur.fetchone() or [0])[0] or 0)

        return staff_rows, payroll_funding, payroll_reserved, payroll_spent

    staff_rows, payroll_funding, payroll_reserved, payroll_spent = run_db(fetch_staff_op)
    if not staff_rows:
        print("No active staff. Add staff first.")
        return

    # (staff_id, jurisdiction_code, hours_regular, hours_overtime, gross, net, emp_tax, emp_other, er_tax, er_other, total_cost)
    items: list[tuple[int, str, float | None, float | None, float, float, float, float, float, float, float]] = []

    print("\nEnter hours for hourly staff (salary is auto-calculated).")
    for (
        staff_id,
        name,
        _title,
        jurisdiction_code,
        pay_type,
        pay_frequency,
        hourly_rate,
        annual_salary,
        overtime_multiplier,
        tax_pct,
        other_pct,
        other_fixed,
        employer_tax_pct,
        employer_other_pct,
        employer_other_fixed,
    ) in staff_rows:
        gross = 0.0
        hours_regular: float | None = None
        hours_overtime: float | None = None

        if str(pay_type) == "hourly":
            rate = float(hourly_rate or 0)
            if rate <= 0:
                print(f"Skipping {name} (hourly rate missing).")
                continue
            print(f"\n{name} (ID {staff_id}) hourly @ ${rate:.2f}/hr")
            hours_regular = prompt_non_negative_amount("  Regular hours: ")
            hours_overtime = prompt_non_negative_amount("  Overtime hours: ")
            gross = float(hours_regular) * rate + float(hours_overtime) * rate * float(overtime_multiplier or 1.5)
        else:
            annual = float(annual_salary or 0)
            if annual <= 0:
                print(f"Skipping {name} (annual salary missing).")
                continue
            periods = _pay_frequency_periods_per_year(str(pay_frequency))
            gross = annual / float(periods)
            print(f"\n{name} (ID {staff_id}) salary {str(pay_frequency)} gross ${gross:.2f}")

        emp_tax_withheld = gross * float(tax_pct or 0)
        emp_other_withheld = gross * float(other_pct or 0) + float(other_fixed or 0)
        net = gross - emp_tax_withheld - emp_other_withheld
        er_tax = gross * float(employer_tax_pct or 0)
        er_other = gross * float(employer_other_pct or 0) + float(employer_other_fixed or 0)
        total_cost = gross + er_tax + er_other
        if net < 0:
            print(f"WARNING: Net pay for {name} is negative; check withholding rates.")

        items.append(
            (
                int(staff_id),
                str(jurisdiction_code or "US-ME"),
                hours_regular,
                hours_overtime,
                float(gross),
                float(net),
                float(emp_tax_withheld),
                float(emp_other_withheld),
                float(er_tax),
                float(er_other),
                float(total_cost),
            )
        )

    if not items:
        print("No payroll items to save.")
        return

    total_gross = sum(i[4] for i in items)
    total_net = sum(i[5] for i in items)
    total_emp_tax = sum(i[6] for i in items)
    total_emp_other = sum(i[7] for i in items)
    total_er_tax = sum(i[8] for i in items)
    total_er_other = sum(i[9] for i in items)
    total_cost = sum(i[10] for i in items)

    print("\nPayroll totals:")
    print(f"Gross: ${total_gross:.2f}")
    print(f"Employee withheld (tax):   ${total_emp_tax:.2f}")
    print(f"Employee withheld (other): ${total_emp_other:.2f}")
    print(f"Net pay:                  ${total_net:.2f}")
    print(f"Employer taxes:           ${total_er_tax:.2f}")
    print(f"Employer other:           ${total_er_other:.2f}")
    print(f"Total payroll cost:       ${total_cost:.2f}")
    print(f"Payroll funding allocated so far (category=payroll): ${payroll_funding:.2f}")

    payroll_available = payroll_funding - payroll_reserved - payroll_spent
    print(f"Payroll reserved (committed): ${payroll_reserved:.2f}")
    print(f"Payroll spent (paid):        ${payroll_spent:.2f}")
    print(f"Payroll available now:       ${payroll_available:.2f}")
    if total_cost > payroll_available + 1e-9:
        print("WARNING: This payroll run would exceed currently available payroll funds.")

    confirm = prompt_optional("Save payroll run? (y/N): ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        return

    def save_op(conn: Any, cur: Any) -> int:
        cur.execute(
            "INSERT INTO payroll_runs (period_start, period_end, run_at, status, paid_at, created_by_user_id) VALUES (?, ?, ?, ?, ?, ?)",
            (
                period_start,
                period_end,
                utc_now_iso(),
                "committed",
                None,
                int(CURRENT_USER["id"]) if CURRENT_USER and CURRENT_USER.get("id") is not None else None,
            ),
        )
        run_id = int(cur.lastrowid)
        for staff_id, jurisdiction_code, hrs_reg, hrs_ot, gross, net, emp_tax, emp_other, er_tax, er_other, item_total_cost in items:
            cur.execute(
                "INSERT INTO payroll_items (payroll_run_id, staff_id, jurisdiction_code, hours_regular, hours_overtime, gross, tax_withheld, other_withheld, net, employer_tax, employer_other, employer_total, total_cost) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    staff_id,
                    jurisdiction_code,
                    hrs_reg,
                    hrs_ot,
                    gross,
                    emp_tax,
                    emp_other,
                    net,
                    er_tax,
                    er_other,
                    (er_tax + er_other),
                    item_total_cost,
                ),
            )
        conn.commit()
        return run_id

    run_id = run_db(save_op, write=True)
    print(f"Payroll run saved as COMMITTED. (Run ID: {run_id})")
    print("Tip: use 'Mark Payroll Run Paid' after funds actually leave the account.")
    audit(
        "payroll.run.create",
        entity_type="payroll_run",
        entity_id=int(run_id),
        details={
            "period_start": period_start,
            "period_end": period_end,
            "total_cost": float(total_cost),
            "item_count": len(items),
        },
    )


@require_role("admin")
def payroll_reports() -> None:
    """Basic payroll reporting (not jurisdiction-specific)."""

    def runs_op(_conn: Any, cur: Any):
        cur.execute(
            "SELECT id, period_start, period_end, run_at, COALESCE(status, 'paid') AS status, paid_at, created_by_user_id "
            "FROM payroll_runs ORDER BY id DESC LIMIT 25"
        )
        return cur.fetchall() or []

    runs = run_db(runs_op)
    if not runs:
        print("No payroll runs yet.")
        return

    formatted: list[tuple[Any, ...]] = []
    for run_id, start, end, run_at, status, paid_at, created_by in runs:
        formatted.append((run_id, start, end, run_at, status, paid_at or "", created_by))

    print("\nPayroll runs (top 25):")
    print_table(["Run ID", "Start", "End", "Run At", "Status", "Paid At", "Created By"], formatted)


@require_role("admin")
def mark_payroll_run_paid() -> None:
    pick = prompt_non_empty("Payroll Run ID to mark PAID: ").strip()
    try:
        run_id_i = int(pick)
    except ValueError:
        print("Invalid Run ID.")
        return

    confirm = prompt_optional(f"Mark Run {run_id_i} as PAID? (y/N): ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        return

    def op(_conn: Any, cur: Any) -> int:
        cur.execute(
            "UPDATE payroll_runs SET status = 'paid', paid_at = ? WHERE id = ?",
            (utc_now_iso(), run_id_i),
        )
        return int(cur.rowcount or 0)

    updated = run_db(op, write=True)
    if updated <= 0:
        print("No payroll run found with that ID.")
        return

    print("Payroll run marked as PAID.")
    audit("payroll.run.paid", entity_type="payroll_run", entity_id=int(run_id_i))

    pick2 = prompt_optional("View details for Run ID (blank to exit): ").strip()
    if not pick2:
        return

    try:
        run_id_i = int(pick2)
    except ValueError:
        print("Invalid Run ID.")
        return

    def details_op(_conn: Any, cur: Any):
        cur.execute(
            "SELECT payroll_items.staff_id, staff.name, payroll_items.jurisdiction_code, "
            "payroll_items.hours_regular, payroll_items.hours_overtime, "
            "payroll_items.gross, payroll_items.tax_withheld, payroll_items.other_withheld, payroll_items.net, "
            "COALESCE(payroll_items.employer_tax, 0), COALESCE(payroll_items.employer_other, 0), COALESCE(payroll_items.total_cost, payroll_items.gross) "
            "FROM payroll_items JOIN staff ON staff.id = payroll_items.staff_id "
            "WHERE payroll_items.payroll_run_id = ? ORDER BY staff.name ASC",
            (run_id_i,),
        )
        return cur.fetchall() or []

    rows = run_db(details_op)
    if not rows:
        print("No items for that run.")
        return

    formatted2: list[tuple[Any, ...]] = []
    total_gross = total_tax = total_other = total_net = 0.0
    total_er_tax = total_er_other = total_cost = 0.0
    for sid, name, jurisdiction_code, hrs_reg, hrs_ot, gross, tax_w, other_w, net, er_tax, er_other, row_cost in rows:
        total_gross += float(gross or 0)
        total_tax += float(tax_w or 0)
        total_other += float(other_w or 0)
        total_net += float(net or 0)
        total_er_tax += float(er_tax or 0)
        total_er_other += float(er_other or 0)
        total_cost += float(row_cost or 0)
        formatted2.append(
            (
                sid,
                name,
                (str(jurisdiction_code or "").strip() or "US-ME"),
                f"{float(hrs_reg or 0):.2f}",
                f"{float(hrs_ot or 0):.2f}",
                f"${float(gross or 0):.2f}",
                f"${float(tax_w or 0):.2f}",
                f"${float(other_w or 0):.2f}",
                f"${float(net or 0):.2f}",
                f"${float(er_tax or 0):.2f}",
                f"${float(er_other or 0):.2f}",
                f"${float(row_cost or 0):.2f}",
            )
        )

    print("\nPayroll items:")
    print_table(
        ["Staff ID", "Name", "Jurisdiction", "Reg Hrs", "OT Hrs", "Gross", "Emp Tax", "Emp Other", "Net", "Er Tax", "Er Other", "Total Cost"],
        formatted2,
    )

    def juris_summary_op(_conn: Any, cur: Any):
        cur.execute(
            "SELECT COALESCE(NULLIF(TRIM(jurisdiction_code), ''), 'US-ME') AS jurisdiction, "
            "COALESCE(SUM(gross), 0) AS gross, COALESCE(SUM(tax_withheld), 0) AS emp_tax, "
            "COALESCE(SUM(other_withheld), 0) AS emp_other, COALESCE(SUM(net), 0) AS net, "
            "COALESCE(SUM(COALESCE(employer_tax, 0)), 0) AS er_tax, "
            "COALESCE(SUM(COALESCE(employer_other, 0)), 0) AS er_other, "
            "COALESCE(SUM(COALESCE(total_cost, gross)), 0) AS total_cost "
            "FROM payroll_items WHERE payroll_run_id = ? "
            "GROUP BY jurisdiction ORDER BY jurisdiction ASC",
            (run_id_i,),
        )
        return cur.fetchall() or []

    juris_rows = run_db(juris_summary_op)
    if juris_rows:
        formatted_j: list[tuple[Any, ...]] = []
        for jurisdiction, gross, emp_tax, emp_other, net, er_tax, er_other, jc_total_cost in juris_rows:
            formatted_j.append(
                (
                    jurisdiction,
                    f"${float(gross or 0):.2f}",
                    f"${float(emp_tax or 0):.2f}",
                    f"${float(emp_other or 0):.2f}",
                    f"${float(net or 0):.2f}",
                    f"${float(er_tax or 0):.2f}",
                    f"${float(er_other or 0):.2f}",
                    f"${float(jc_total_cost or 0):.2f}",
                )
            )
        print("\nBy jurisdiction:")
        print_table(["Jurisdiction", "Gross", "Emp Tax", "Emp Other", "Net", "Er Tax", "Er Other", "Total Cost"], formatted_j)

    print("\nTotals:")
    print(f"Gross: ${total_gross:.2f}")
    print(f"Emp Tax:   ${total_tax:.2f}")
    print(f"Emp Other: ${total_other:.2f}")
    print(f"Net:       ${total_net:.2f}")
    print(f"Er Tax:    ${total_er_tax:.2f}")
    print(f"Er Other:  ${total_er_other:.2f}")
    print(f"Total cost:${total_cost:.2f}")
