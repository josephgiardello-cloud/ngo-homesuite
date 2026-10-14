from __future__ import annotations

import re
from typing import Any

from ..auth.session import require_role
from ..db.connection import run_db
from ..db.utils import print_table
from ..prompts import (
    prompt_non_empty,
    prompt_non_negative_amount,
    prompt_optional,
    prompt_positive_amount,
)


@require_role("admin")
def add_staff() -> None:
    name = prompt_non_empty("Staff name: ")
    title = prompt_optional("Title/Role (optional): ")

    # Maine default, but supports other locations later (e.g., US-CA, CA-ON).
    jurisdiction_code = prompt_optional("Jurisdiction code (default US-ME): ").strip().upper()
    if not jurisdiction_code:
        jurisdiction_code = "US-ME"

    # Light validation: allow A-Z, 0-9, dash.
    if re.fullmatch(r"[A-Z0-9]{2,3}(-[A-Z0-9]{1,3})?", jurisdiction_code) is None:
        print("Jurisdiction code doesn't look valid (examples: US-ME, US-CA, CA-ON).")
        return

    while True:
        pay_type = prompt_non_empty("Pay type (hourly/salary): ").strip().lower()
        if pay_type in {"hourly", "h"}:
            pay_type = "hourly"
            break
        if pay_type in {"salary", "s", "salaried"}:
            pay_type = "salary"
            break
        print("Please enter 'hourly' or 'salary'.")

    while True:
        pay_frequency = prompt_non_empty("Pay frequency (weekly/biweekly/monthly): ").strip().lower()
        if pay_frequency in {"weekly", "biweekly", "monthly"}:
            break
        print("Please enter weekly, biweekly, or monthly.")

    hourly_rate: float | None = None
    annual_salary: float | None = None
    if pay_type == "hourly":
        hourly_rate = prompt_positive_amount("Hourly rate: ")
    else:
        annual_salary = prompt_positive_amount("Annual salary: ")

    overtime_multiplier = prompt_non_negative_amount("Overtime multiplier (default 1.5): ")
    if overtime_multiplier == 0:
        overtime_multiplier = 1.5

    tax_pct = prompt_non_negative_amount("Tax withholding % (e.g. 0.15 for 15%): ")
    other_pct = prompt_non_negative_amount("Other withholding % (optional, 0 for none): ")
    other_fixed = prompt_non_negative_amount("Other fixed withholding per period (optional): ")

    # Employer-side costs (configurable; set to 0 if unknown).
    employer_tax_pct = prompt_non_negative_amount("Employer tax % (optional, 0 for none): ")
    employer_other_pct = prompt_non_negative_amount("Employer other % (optional, 0 for none): ")
    employer_other_fixed = prompt_non_negative_amount("Employer other fixed per period (optional): ")

    start_date = prompt_optional("Start date YYYY-MM-DD (optional): ").strip()
    if start_date and re.fullmatch(r"\d{4}-\d{2}-\d{2}", start_date) is None:
        print("Start date ignored (use YYYY-MM-DD).")
        start_date = ""

    notes = prompt_optional("Notes (optional): ")

    def op(_conn: Any, cur: Any) -> int:
        cur.execute(
            "INSERT INTO staff (name, title, jurisdiction_code, pay_type, pay_frequency, hourly_rate, annual_salary, "
            "overtime_multiplier, tax_withholding_pct, other_withholding_pct, other_withholding_fixed, "
            "employer_tax_pct, employer_other_pct, employer_other_fixed, "
            "active, start_date, end_date, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, NULL, ?)",
            (
                name,
                title,
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
                start_date or None,
                notes,
            ),
        )
        return int(cur.lastrowid)

    staff_id = run_db(op, write=True)
    print(f"Staff added! (ID: {staff_id})")


@require_role("admin")
def view_staff(active_only: bool = True) -> None:
    def op(_conn: Any, cur: Any):
        if active_only:
            cur.execute(
                "SELECT id, name, title, jurisdiction_code, pay_type, pay_frequency, hourly_rate, annual_salary, "
                "tax_withholding_pct, employer_tax_pct, active "
                "FROM staff WHERE active = 1 ORDER BY id DESC"
            )
        else:
            cur.execute(
                "SELECT id, name, title, jurisdiction_code, pay_type, pay_frequency, hourly_rate, annual_salary, "
                "tax_withholding_pct, employer_tax_pct, active "
                "FROM staff ORDER BY id DESC"
            )
        return cur.fetchall()

    rows = run_db(op)
    if not rows:
        print("No staff found.")
        return

    formatted: list[tuple[Any, ...]] = []
    for (
        sid,
        name,
        title,
        jurisdiction_code,
        pay_type,
        pay_frequency,
        hourly_rate,
        annual_salary,
        tax_pct,
        employer_tax_pct,
        active,
    ) in rows:
        pay = ""
        try:
            if str(pay_type) == "hourly":
                pay = f"${float(hourly_rate or 0):.2f}/hr"
            else:
                pay = f"${float(annual_salary or 0):.2f}/yr"
        except (TypeError, ValueError):
            pay = ""

        try:
            emp_tax_str = f"{float(tax_pct or 0) * 100:.1f}%"
        except (TypeError, ValueError):
            emp_tax_str = ""
        try:
            er_tax_str = f"{float(employer_tax_pct or 0) * 100:.1f}%"
        except (TypeError, ValueError):
            er_tax_str = ""

        formatted.append(
            (
                sid,
                name,
                title,
                (str(jurisdiction_code or "").strip() or "US-ME"),
                pay_type,
                pay_frequency,
                pay,
                emp_tax_str,
                er_tax_str,
                "yes" if int(active or 0) == 1 else "no",
            )
        )

    print_table(
        ["ID", "Name", "Title", "Jurisdiction", "Type", "Frequency", "Pay", "Emp Tax %", "Er Tax %", "Active"],
        formatted,
    )
