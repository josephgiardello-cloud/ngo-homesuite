from __future__ import annotations

from typing import Any

from ..auth.session import require_role
from ..db.connection import run_db
from ..db.utils import print_table, show_db_health
from ..prompts import parse_date_iso_to_date, prompt_optional


@require_role("admin", "fundraiser", "viewer")
def db_health_check() -> None:
    """Quick DB connectivity check."""

    show_db_health()


@require_role("admin", "fundraiser", "viewer")
def view_funding_summary() -> None:
    """Show funds by project + payroll + general + unassigned."""

    def op(_conn: Any, cur: Any):
        cur.execute("SELECT COALESCE(SUM(amount), 0) FROM donations")
        total_donations = float((cur.fetchone() or [0])[0] or 0)

        cur.execute("SELECT COALESCE(SUM(amount), 0) FROM donation_allocations")
        total_allocated = float((cur.fetchone() or [0])[0] or 0)

        cur.execute("SELECT COALESCE(SUM(amount), 0) FROM donation_allocations WHERE category = 'payroll'")
        payroll = float((cur.fetchone() or [0])[0] or 0)

        # Payroll reserved/spent: split by payroll_runs.status.
        # Legacy runs (status NULL) are treated as 'paid'.
        cur.execute(
            "SELECT COALESCE(SUM(COALESCE(pi.total_cost, pi.gross)), 0) "
            "FROM payroll_items pi JOIN payroll_runs pr ON pr.id = pi.payroll_run_id "
            "WHERE COALESCE(pr.status, 'paid') = 'committed'"
        )
        payroll_reserved_total_cost = float((cur.fetchone() or [0])[0] or 0)

        cur.execute(
            "SELECT COALESCE(SUM(COALESCE(pi.total_cost, pi.gross)), 0) "
            "FROM payroll_items pi JOIN payroll_runs pr ON pr.id = pi.payroll_run_id "
            "WHERE COALESCE(pr.status, 'paid') = 'paid'"
        )
        payroll_spent_total_cost = float((cur.fetchone() or [0])[0] or 0)

        cur.execute(
            "SELECT COALESCE(SUM(pi.net), 0) "
            "FROM payroll_items pi JOIN payroll_runs pr ON pr.id = pi.payroll_run_id "
            "WHERE COALESCE(pr.status, 'paid') = 'paid'"
        )
        payroll_spent_net = float((cur.fetchone() or [0])[0] or 0)

        cur.execute("SELECT COALESCE(SUM(amount), 0) FROM donation_allocations WHERE category = 'general'")
        general_assigned = float((cur.fetchone() or [0])[0] or 0)

        cur.execute(
            "SELECT projects.id, projects.name, COALESCE(SUM(donation_allocations.amount), 0) "
            "FROM projects "
            "LEFT JOIN donation_allocations ON donation_allocations.project_id = projects.id "
            "AND donation_allocations.category = 'project' "
            "GROUP BY projects.id, projects.name "
            "ORDER BY projects.id DESC"
        )
        by_project = cur.fetchall() or []

        cur.execute(
            "SELECT id, name, COALESCE(budget, 0), COALESCE(spent, 0), start_date "
            "FROM projects ORDER BY id DESC"
        )
        projects_budget_spent = cur.fetchall() or []

        cur.execute(
            "SELECT donations.id, donors.name, donations.amount, donations.date, donations.purpose, "
            "COALESCE(SUM(donation_allocations.amount), 0) AS allocated "
            "FROM donations "
            "JOIN donors ON donors.id = donations.donor_id "
            "LEFT JOIN donation_allocations ON donation_allocations.donation_id = donations.id "
            "GROUP BY donations.id "
            "HAVING (donations.amount - allocated) > 0.000001 "
            "ORDER BY donations.id DESC "
            "LIMIT 25"
        )
        unassigned = cur.fetchall() or []

        return (
            total_donations,
            total_allocated,
            payroll,
            payroll_reserved_total_cost,
            payroll_spent_total_cost,
            payroll_spent_net,
            general_assigned,
            by_project,
            projects_budget_spent,
            unassigned,
        )

    (
        total_donations,
        total_allocated,
        payroll,
        payroll_reserved_total_cost,
        payroll_spent_total_cost,
        payroll_spent_net,
        general_assigned,
        by_project,
        projects_budget_spent,
        unassigned,
    ) = run_db(op)

    unassigned_total = max(0.0, total_donations - total_allocated)
    print("\nFunding Summary")
    print(f"Total donations: ${total_donations:.2f}")
    print(f"Allocated total: ${total_allocated:.2f}")
    print(f"Unassigned (general fund to be assigned): ${unassigned_total:.2f}")
    print(f"Payroll allocated: ${payroll:.2f}")
    print("Note: COMMITTED payroll runs reserve funds until you mark them PAID.")
    print(f"Payroll reserved (committed): ${payroll_reserved_total_cost:.2f}")
    print(f"Payroll spent (total cost): ${payroll_spent_total_cost:.2f}")
    print(f"Payroll spent (net pay): ${payroll_spent_net:.2f}")

    payroll_available = payroll - payroll_reserved_total_cost - payroll_spent_total_cost
    print(f"Payroll available: ${payroll_available:.2f}")
    if payroll_spent_total_cost > payroll + 1e-9:
        print("WARNING: Payroll spending exceeds payroll allocations.")
    if payroll_available < -1e-9:
        print("WARNING: Payroll commitments + spending exceed payroll allocations.")

    print(f"General allocated: ${general_assigned:.2f}")
    general_available = unassigned_total + general_assigned
    print(f"General available (unassigned + allocated): ${general_available:.2f}")

    if by_project:
        rows: list[tuple[Any, ...]] = []
        for pid, name, amount in by_project:
            rows.append((pid, name, f"${float(amount or 0):.2f}"))
        print("\nBy project:")
        print_table(["Project ID", "Project", "Allocated"], rows)
    else:
        print("\nBy project: (no projects)")

    if projects_budget_spent:
        rows3: list[tuple[Any, ...]] = []
        over_budget = 0
        for pid, name, budget, spent, start_date in projects_budget_spent:
            try:
                budget_f = float(budget or 0)
            except (TypeError, ValueError):
                budget_f = 0.0
            try:
                spent_f = float(spent or 0)
            except (TypeError, ValueError):
                spent_f = 0.0
            remaining_f = budget_f - spent_f
            if remaining_f < -1e-9:
                over_budget += 1
            rows3.append(
                (
                    pid,
                    name,
                    f"${budget_f:.2f}",
                    f"${spent_f:.2f}",
                    f"${remaining_f:.2f}",
                    start_date,
                )
            )
        print("\nProject budget vs spent:")
        print_table(["Project ID", "Project", "Budget", "Spent", "Remaining", "Start"], rows3)
        if over_budget:
            print(f"WARNING: {over_budget} project(s) are over budget.")

    if unassigned:
        rows2: list[tuple[Any, ...]] = []
        for did, donor_name, amt, date, purpose, allocated in unassigned:
            try:
                amt_f = float(amt or 0)
                alloc_f = float(allocated or 0)
            except (TypeError, ValueError):
                continue
            rows2.append((did, donor_name, f"${amt_f:.2f}", f"${(amt_f - alloc_f):.2f}", date, purpose))
        if rows2:
            print("\nDonations with unassigned remainder (top 25):")
            print_table(["Donation ID", "Donor", "Amount", "Unassigned", "Date", "Purpose"], rows2)


@require_role("admin")
def view_audit_log() -> None:
    """View recent audit log entries with optional filters."""

    action_filter = prompt_optional("Action contains (optional): ").strip()
    user_raw = prompt_optional("User ID (optional): ").strip()
    user_id: int | None = None
    if user_raw:
        try:
            user_id = int(user_raw)
        except ValueError:
            print("Invalid user ID.")
            return

    start_raw = prompt_optional("Start date (YYYY-MM-DD, optional): ").strip()
    end_raw = prompt_optional("End date (YYYY-MM-DD, optional): ").strip()

    start_date = start_raw if start_raw and parse_date_iso_to_date(start_raw) else ""
    end_date = end_raw if end_raw and parse_date_iso_to_date(end_raw) else ""
    if start_raw and not start_date:
        print("Invalid start date.")
        return
    if end_raw and not end_date:
        print("Invalid end date.")
        return

    limit_raw = prompt_optional("Limit (default 50, max 500): ").strip()
    limit = 50
    if limit_raw:
        try:
            limit = int(limit_raw)
        except ValueError:
            print("Invalid limit.")
            return
    limit = max(1, min(500, limit))

    def _truncate(value: Any, max_len: int) -> str:
        s = "" if value is None else str(value)
        if len(s) <= max_len:
            return s
        if max_len <= 3:
            return s[:max_len]
        return s[: max_len - 3] + "..."

    def op(_conn: Any, cur: Any):
        sql = (
            "SELECT a.at_utc, COALESCE(a.username, u.username, ''), COALESCE(a.role, ''), a.action, "
            "COALESCE(a.entity_type, ''), COALESCE(a.entity_id, ''), COALESCE(a.details_json, '') "
            "FROM audit_log a LEFT JOIN users u ON u.id = a.user_id WHERE 1=1"
        )
        params: list[Any] = []

        if action_filter:
            sql += " AND a.action LIKE ?"
            params.append(f"%{action_filter}%")
        if user_id is not None:
            sql += " AND a.user_id = ?"
            params.append(int(user_id))
        if start_date:
            sql += " AND substr(a.at_utc, 1, 10) >= ?"
            params.append(start_date)
        if end_date:
            sql += " AND substr(a.at_utc, 1, 10) <= ?"
            params.append(end_date)

        sql += " ORDER BY a.id DESC LIMIT ?"
        params.append(int(limit))

        cur.execute(sql, tuple(params))
        return cur.fetchall() or []

    rows = run_db(op)
    if not rows:
        print("No audit entries found for the given filters.")
        return

    formatted: list[tuple[Any, ...]] = []
    for at_utc, username, role, action, entity_type, entity_id, details_json in rows:
        who = (str(username).strip() if username else "") or "(system)"
        formatted.append(
            (
                _truncate(at_utc, 25),
                _truncate(who, 20),
                _truncate(role, 12),
                _truncate(action, 40),
                _truncate(entity_type, 16),
                _truncate(entity_id, 10),
                _truncate(details_json, 80),
            )
        )

    print("\nAudit Log (most recent first)")
    print_table(["At (UTC)", "User", "Role", "Action", "Entity", "ID", "Details"], formatted)
