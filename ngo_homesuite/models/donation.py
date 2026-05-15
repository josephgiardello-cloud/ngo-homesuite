from __future__ import annotations

import sqlite3
from typing import Any

from ..auth.session import require_role
from ..db.connection import run_db
from ..db.utils import audit, print_table
from ..prompts import (
    prompt_int,
    prompt_non_empty,
    prompt_optional,
    prompt_positive_amount,
    prompt_positive_amount_allowing_zero,
    utc_today_iso,
)

from .donor import _donor_exists, find_donors
from .campaign import view_campaigns


@require_role("admin", "fundraiser")
def record_donation() -> None:
    raw_id = prompt_optional("Donor ID (leave blank to search): ")
    if not raw_id:
        find_donors()
        raw_id = prompt_non_empty("Donor ID to use: ")

    try:
        donor_id = int(raw_id)
    except ValueError:
        print("Invalid donor ID.")
        return

    def donor_exists_op(_conn: Any, cur: Any) -> bool:
        return _donor_exists(cur, donor_id)

    if not run_db(donor_exists_op):
        print("Donor not found. Add the donor first, then record the donation.")
        return

    amount = prompt_positive_amount("Amount: ")
    purpose = prompt_optional("Purpose/Campaign (optional): ")
    if purpose and len(purpose) > 250:
        print("Purpose too long (max 250 chars).")
        return

    raw_campaign = prompt_optional("Campaign ID (optional; blank for none, 'list' to view): ").strip()
    if raw_campaign.lower() in {"list", "l", "?"}:
        view_campaigns()
        raw_campaign = prompt_optional("Campaign ID (optional; blank for none): ").strip()

    campaign_id: int | None = None
    if raw_campaign:
        try:
            campaign_id = int(raw_campaign)
        except ValueError:
            print("Invalid campaign ID.")
            return

        def campaign_exists_op(_conn: Any, cur: Any) -> bool:
            cur.execute("SELECT 1 FROM campaigns WHERE id = ?", (campaign_id,))
            return cur.fetchone() is not None

        if not run_db(campaign_exists_op):
            print("Campaign not found.")
            return

    raw_pledge = prompt_optional("Pledge ID (optional; blank for none): ").strip()
    pledge_id: int | None = None
    if raw_pledge:
        try:
            pledge_id = int(raw_pledge)
        except ValueError:
            print("Invalid pledge ID.")
            return

        def pledge_fetch_op(_conn: Any, cur: Any):
            cur.execute(
                "SELECT id, donor_id, campaign_id, active FROM pledges WHERE id = ?",
                (pledge_id,),
            )
            return cur.fetchone()

        pledge_row = run_db(pledge_fetch_op)
        if not pledge_row:
            print("Pledge not found.")
            return

        _pid, pledge_donor_id, pledge_campaign_id, pledge_active = pledge_row
        if int(pledge_donor_id) != int(donor_id):
            print("That pledge belongs to a different donor.")
            return
        if int(pledge_active or 0) != 1:
            print("WARNING: This pledge is not active.")

        if campaign_id is None and pledge_campaign_id is not None:
            try:
                campaign_id = int(pledge_campaign_id)
            except (TypeError, ValueError):
                campaign_id = None

    date = utc_today_iso()

    def op_ret(conn: Any, cur: Any) -> int:
        cur.execute(
            "INSERT INTO donations (donor_id, campaign_id, pledge_id, amount, date, purpose) VALUES (?, ?, ?, ?, ?, ?)",
            (donor_id, campaign_id, pledge_id, amount, date, purpose),
        )
        conn.commit()
        return int(cur.lastrowid)

    try:
        donation_id = run_db(op_ret, write=True)
    except sqlite3.IntegrityError as e:
        print(f"Could not record donation: {e}")
        return

    print("Donation recorded!")
    audit(
        "donation.create",
        entity_type="donation",
        entity_id=int(donation_id),
        details={
            "donor_id": int(donor_id),
            "campaign_id": int(campaign_id) if campaign_id is not None else None,
            "pledge_id": int(pledge_id) if pledge_id is not None else None,
            "amount": float(amount),
            "date": date,
        },
    )


@require_role("admin", "fundraiser")
def allocate_donation_multi() -> None:
    donation_id = prompt_int("Donation ID to allocate: ")

    def fetch_op(_conn: Any, cur: Any):
        cur.execute(
            "SELECT donations.id, donors.name, donations.amount, donations.date, donations.purpose "
            "FROM donations JOIN donors ON donors.id = donations.donor_id WHERE donations.id = ?",
            (donation_id,),
        )
        donation = cur.fetchone()
        if not donation:
            return None, [], []

        cur.execute("SELECT id, name FROM projects ORDER BY id DESC")
        projects = cur.fetchall() or []

        cur.execute(
            "SELECT category, project_id, amount FROM donation_allocations WHERE donation_id = ? ORDER BY id ASC",
            (donation_id,),
        )
        existing = cur.fetchall() or []
        return donation, projects, existing

    donation, projects, existing = run_db(fetch_op)
    if not donation:
        print("Donation not found.")
        return

    _did, donor_name, donation_amount, donation_date, donation_purpose = donation
    try:
        donation_amount_f = float(donation_amount)
    except (TypeError, ValueError):
        print("Donation amount is not a valid number; cannot allocate.")
        return

    print("\nDonation:")
    print_table(
        ["Donation ID", "Donor", "Amount", "Date", "Purpose"],
        [(donation_id, donor_name, f"${donation_amount_f:.2f}", donation_date, donation_purpose)],
    )

    if existing:
        formatted_existing: list[tuple[Any, ...]] = []
        for category, project_id, amount in existing:
            label = str(category)
            if str(category) == "project" and project_id is not None:
                name = next((p[1] for p in projects if int(p[0]) == int(project_id)), None)
                label = f"project:{name or project_id}"
            try:
                amt = f"${float(amount):.2f}"
            except (TypeError, ValueError):
                amt = str(amount)
            formatted_existing.append((label, amt))
        print("\nExisting allocations (will be replaced if you save):")
        print_table(["Allocation", "Amount"], formatted_existing)

    if projects:
        print("\nProjects:")
        print_table(["Project ID", "Name"], [(pid, name) for pid, name in projects])
    else:
        print("\nNo projects exist yet (you can still allocate to payroll/general).")

    allocations: list[tuple[str, int | None, float]] = []
    remaining = donation_amount_f
    print("\nEnter allocations. Leave type blank to finish.")

    while True:
        raw_type = prompt_optional("Allocation type (project/payroll/general) [blank to finish]: ").strip().lower()
        if not raw_type:
            break

        if raw_type in {"project", "p"}:
            category = "project"
        elif raw_type in {"payroll", "pay", "pr"}:
            category = "payroll"
        elif raw_type in {"general", "g"}:
            category = "general"
        else:
            print("Please enter 'project', 'payroll', or 'general'.")
            continue

        project_id: int | None = None
        if category == "project":
            project_id = prompt_int("Project ID: ")
            if projects and not any(int(pid) == int(project_id) for pid, _n in projects):
                print("Project not found.")
                continue

        if remaining <= 0:
            print("No remaining amount to allocate.")
            break

        amt = prompt_positive_amount_allowing_zero(f"Amount to allocate (remaining ${remaining:.2f}): ")
        if amt <= 0:
            continue
        if amt > remaining + 1e-9:
            print("That exceeds the remaining amount.")
            continue

        allocations.append((category, project_id, float(amt)))
        remaining -= float(amt)
        print(f"Remaining: ${remaining:.2f}")

    allocated_total = sum(a[2] for a in allocations)
    print("\nSummary:")
    print(f"Donation: ${donation_amount_f:.2f}")
    print(f"Allocated: ${allocated_total:.2f}")
    print(f"Unassigned (to be allocated later): ${donation_amount_f - allocated_total:.2f}")

    confirm = prompt_optional("Save allocations? (y/N): ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        return

    def save_op(conn: Any, cur: Any) -> None:
        cur.execute("DELETE FROM donation_allocations WHERE donation_id = ?", (donation_id,))
        for category, project_id, amount in allocations:
            cur.execute(
                "INSERT INTO donation_allocations (donation_id, project_id, category, amount) VALUES (?, ?, ?, ?)",
                (donation_id, project_id, category, amount),
            )
        conn.commit()

    run_db(save_op, write=True)
    print("Allocations saved.")
    audit(
        "donation.allocate.multi",
        entity_type="donation",
        entity_id=int(donation_id),
        details={"allocated_total": float(allocated_total), "allocation_count": len(allocations)},
    )


@require_role("admin", "fundraiser", "viewer")
def view_total_donations() -> None:
    def op(_conn: Any, cur: Any):
        cur.execute("SELECT SUM(amount) FROM donations")
        return cur.fetchone()

    row = run_db(op)
    total = (row[0] if row else 0) or 0
    print(f"Total donations: ${total:.2f}")


@require_role("admin", "fundraiser", "viewer")
def view_recent_donations(limit: int = 20) -> None:
    def op(_conn: Any, cur: Any):
        cur.execute(
            "SELECT donations.id, donors.name, COALESCE(campaigns.name, ''), COALESCE(donations.pledge_id, ''), donations.amount, donations.date, donations.purpose "
            "FROM donations "
            "JOIN donors ON donors.id = donations.donor_id "
            "LEFT JOIN campaigns ON campaigns.id = donations.campaign_id "
            "ORDER BY donations.id DESC LIMIT ?",
            (limit,),
        )
        return cur.fetchall()

    rows = run_db(op)
    if not rows:
        print("No donations recorded yet.")
        return

    formatted_rows: list[tuple[Any, ...]] = []
    for donation_id, donor_name, campaign_name, pledge_id, amount, date, purpose in rows:
        try:
            amount_str = f"${float(amount):.2f}"
        except (TypeError, ValueError):
            amount_str = str(amount)
        formatted_rows.append((donation_id, donor_name, campaign_name or "", pledge_id or "", amount_str, date, purpose))

    print_table(["Donation ID", "Donor", "Campaign", "Pledge", "Amount", "Date", "Purpose"], formatted_rows)


@require_role("admin", "fundraiser", "viewer")
def view_donations_by_donor() -> None:
    donor_id = prompt_int("Donor ID: ")

    def exists_op(_conn: Any, cur: Any) -> bool:
        return _donor_exists(cur, donor_id)

    if not run_db(exists_op):
        print("Donor not found.")
        return

    def donor_op(_conn: Any, cur: Any):
        cur.execute("SELECT id, name, email, phone FROM donors WHERE id = ?", (donor_id,))
        return cur.fetchone()

    donor = run_db(donor_op)
    print("Donor:")
    if donor:
        print_table(["ID", "Name", "Email", "Phone"], [donor])
    else:
        print("(not found)")
        return

    def donations_op(_conn: Any, cur: Any):
        cur.execute(
            "SELECT donations.id, COALESCE(campaigns.name, ''), COALESCE(donations.pledge_id, ''), donations.amount, donations.date, donations.purpose "
            "FROM donations LEFT JOIN campaigns ON campaigns.id = donations.campaign_id "
            "WHERE donations.donor_id = ? ORDER BY donations.id DESC",
            (donor_id,),
        )
        return cur.fetchall()

    rows = run_db(donations_op)
    if not rows:
        print("No donations for this donor yet.")
        return

    total = 0.0
    formatted_rows2: list[tuple[Any, ...]] = []
    for donation_id, campaign_name, pledge_id, amount, date, purpose in rows:
        try:
            amount_f = float(amount)
            amount_str = f"${amount_f:.2f}"
            total += amount_f
        except (TypeError, ValueError):
            amount_str = str(amount)
        formatted_rows2.append((donation_id, campaign_name or "", pledge_id or "", amount_str, date, purpose))

    print_table(["Donation ID", "Campaign", "Pledge", "Amount", "Date", "Purpose"], formatted_rows2)
    print(f"Total from donor: ${total:.2f}")
