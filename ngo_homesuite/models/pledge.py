from __future__ import annotations

from datetime import date
from typing import Any

from ..auth.session import CURRENT_USER, require_role
from ..db.connection import run_db
from ..db.utils import audit, print_table
from ..prompts import (
    parse_date_iso_to_date,
    prompt_date_iso,
    prompt_int,
    prompt_non_empty,
    prompt_optional,
    prompt_positive_amount,
    utc_now_iso,
    utc_today_iso,
)

from .campaign import view_campaigns
from .donor import _donor_exists


def _count_expected_occurrences(start_iso: str, end_iso: str | None, freq: str, as_of_iso: str) -> int:
    """Count expected occurrences from start through min(end, as_of), inclusive."""

    start_d = parse_date_iso_to_date(start_iso)
    as_of_d = parse_date_iso_to_date(as_of_iso)
    if not start_d or not as_of_d:
        return 0

    cutoff = as_of_d
    if end_iso:
        end_d = parse_date_iso_to_date(end_iso)
        if end_d and end_d < cutoff:
            cutoff = end_d

    if cutoff < start_d:
        return 0

    freq_n = freq.strip().lower()
    if freq_n in {"once", "one", "one-time", "one_time", "single"}:
        return 1
    if freq_n in {"weekly", "week"}:
        days = (cutoff - start_d).days
        return (days // 7) + 1
    if freq_n in {"monthly", "month"}:
        months = (cutoff.year - start_d.year) * 12 + (cutoff.month - start_d.month)
        if cutoff.day < start_d.day:
            months -= 1
        return months + 1
    if freq_n in {"quarterly", "quarter"}:
        months = (cutoff.year - start_d.year) * 12 + (cutoff.month - start_d.month)
        if cutoff.day < start_d.day:
            months -= 1
        return (months // 3) + 1
    if freq_n in {"annual", "yearly", "year"}:
        years = cutoff.year - start_d.year
        if (cutoff.month, cutoff.day) < (start_d.month, start_d.day):
            years -= 1
        return years + 1

    return 0


@require_role("admin", "fundraiser")
def add_pledge() -> None:
    donor_id = prompt_int("Donor ID: ")

    def exists_op(_conn: Any, cur: Any) -> bool:
        return _donor_exists(cur, donor_id)

    if not run_db(exists_op):
        print("Donor not found.")
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

    amount = prompt_positive_amount("Pledge amount (per period): ")
    freq = prompt_non_empty("Frequency (one-time/weekly/monthly/quarterly/annual): ").strip().lower()
    if freq not in {"one-time", "weekly", "monthly", "quarterly", "annual"}:
        print("Invalid frequency.")
        return

    start_date = prompt_date_iso("Start date (YYYY-MM-DD): ")
    end_raw = prompt_optional("End date (YYYY-MM-DD, optional): ").strip()
    end_date: str | None = None
    if end_raw:
        end_date = prompt_date_iso("End date (YYYY-MM-DD): ")

    active_raw = prompt_optional("Active? (Y/n): ").strip().lower()
    active = 0 if active_raw == "n" else 1

    notes = prompt_optional("Notes (optional): ")
    if notes and len(notes) > 2000:
        print("Notes too long (max 2000 chars).")
        return

    def op(conn: Any, cur: Any) -> int:
        cur.execute(
            "INSERT INTO pledges (donor_id, campaign_id, amount, frequency, start_date, end_date, active, notes, created_at, created_by_user_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                donor_id,
                campaign_id,
                amount,
                freq,
                start_date,
                end_date,
                active,
                notes,
                utc_now_iso(),
                int(CURRENT_USER["id"]) if CURRENT_USER and CURRENT_USER.get("id") is not None else None,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)

    pid = run_db(op, write=True)
    print(f"Pledge added. (ID: {pid})")
    audit(
        "pledge.create",
        entity_type="pledge",
        entity_id=int(pid),
        details={
            "donor_id": int(donor_id),
            "campaign_id": int(campaign_id) if campaign_id is not None else None,
            "frequency": freq,
        },
    )


@require_role("admin", "fundraiser", "viewer")
def view_pledges() -> None:
    donor_raw = prompt_optional("Filter by Donor ID (optional): ").strip()
    donor_id: int | None = None
    if donor_raw:
        try:
            donor_id = int(donor_raw)
        except ValueError:
            print("Invalid donor ID.")
            return

    def op(_conn: Any, cur: Any):
        if donor_id is None:
            cur.execute(
                "SELECT p.id, d.name, COALESCE(c.name, ''), p.amount, p.frequency, p.start_date, COALESCE(p.end_date, ''), p.active "
                "FROM pledges p JOIN donors d ON d.id = p.donor_id "
                "LEFT JOIN campaigns c ON c.id = p.campaign_id "
                "ORDER BY p.id DESC LIMIT 50"
            )
        else:
            cur.execute(
                "SELECT p.id, d.name, COALESCE(c.name, ''), p.amount, p.frequency, p.start_date, COALESCE(p.end_date, ''), p.active "
                "FROM pledges p JOIN donors d ON d.id = p.donor_id "
                "LEFT JOIN campaigns c ON c.id = p.campaign_id "
                "WHERE p.donor_id = ? ORDER BY p.id DESC LIMIT 50",
                (donor_id,),
            )
        return cur.fetchall() or []

    rows = run_db(op)
    if not rows:
        print("No pledges found.")
        return

    formatted: list[tuple[Any, ...]] = []
    for pid, donor_name, campaign_name, amount, freq, start, end, active in rows:
        try:
            amt_str = f"${float(amount or 0):.2f}"
        except (TypeError, ValueError):
            amt_str = str(amount)
        formatted.append(
            (pid, donor_name, campaign_name or "", amt_str, freq, start, end, "yes" if int(active or 0) == 1 else "no")
        )

    print_table(["Pledge ID", "Donor", "Campaign", "Amount", "Frequency", "Start", "End", "Active"], formatted)


@require_role("admin", "fundraiser", "viewer")
def pledge_report() -> None:
    as_of = utc_today_iso()

    def op(_conn: Any, cur: Any):
        cur.execute(
            "SELECT p.id, p.donor_id, d.name, COALESCE(c.name, ''), p.amount, p.frequency, p.start_date, p.end_date, p.active "
            "FROM pledges p JOIN donors d ON d.id = p.donor_id "
            "LEFT JOIN campaigns c ON c.id = p.campaign_id "
            "ORDER BY p.id DESC"
        )
        pledges = cur.fetchall() or []
        cur.execute(
            "SELECT pledge_id, COALESCE(SUM(amount), 0) FROM donations "
            "WHERE pledge_id IS NOT NULL AND date <= ? GROUP BY pledge_id",
            (as_of,),
        )
        received_map = {int(pid): float(total or 0) for pid, total in (cur.fetchall() or [])}
        return pledges, received_map

    pledges, received_map = run_db(op)
    if not pledges:
        print("No pledges yet.")
        return

    rows: list[tuple[Any, ...]] = []
    total_expected = 0.0
    total_received = 0.0

    for pid, _donor_id, donor_name, campaign_name, amount, freq, start_date, end_date, active in pledges:
        occ = _count_expected_occurrences(str(start_date), (str(end_date) if end_date else None), str(freq), as_of)
        expected = float(amount or 0) * float(occ)
        received = float(received_map.get(int(pid), 0.0))
        balance = expected - received
        total_expected += expected
        total_received += received
        rows.append(
            (
                int(pid),
                donor_name,
                campaign_name or "",
                str(freq),
                str(start_date),
                (str(end_date) if end_date else ""),
                "yes" if int(active or 0) == 1 else "no",
                int(occ),
                f"${expected:.2f}",
                f"${received:.2f}",
                f"${balance:.2f}",
            )
        )

    print("\nPledge Report (Expected vs Received)")
    print(f"As of: {as_of}")
    print_table(
        ["Pledge ID", "Donor", "Campaign", "Freq", "Start", "End", "Active", "Expected #", "Expected $", "Received $", "Balance $"],
        rows,
    )
    print(f"\nTotals expected: ${total_expected:.2f}")
    print(f"Totals received: ${total_received:.2f}")
    print(f"Totals balance:  ${(total_expected - total_received):.2f}")
