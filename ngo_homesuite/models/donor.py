from __future__ import annotations

import sqlite3
from typing import Any

from ..auth.session import CURRENT_USER, require_role
from ..db.connection import run_db
from ..db.utils import audit, print_table
from ..prompts import (
    looks_like_email,
    looks_like_phone,
    normalize_optional_email,
    normalize_optional_phone,
    normalize_tag,
    prompt_non_empty,
    prompt_optional,
    prompt_int,
    utc_now_iso,
)


def _donor_exists(cur: Any, donor_id: int) -> bool:
    cur.execute("SELECT 1 FROM donors WHERE id = ?", (donor_id,))
    return cur.fetchone() is not None


@require_role("admin", "fundraiser", "viewer")
def find_donors() -> None:
    query = prompt_non_empty("Search donors (name/email/phone): ")
    like = f"%{query}%"

    def op(_conn: Any, cur: Any):
        cur.execute(
            "SELECT id, name, email, phone FROM donors "
            "WHERE name LIKE ? OR email LIKE ? OR phone LIKE ? "
            "ORDER BY id DESC",
            (like, like, like),
        )
        return cur.fetchall()

    rows = run_db(op)
    if not rows:
        print("No donors found.")
        return
    print("\nMatches:")
    print_table(["ID", "Name", "Email", "Phone"], rows)


@require_role("admin", "fundraiser")
def add_donor() -> None:
    name = prompt_non_empty("Donor name: ")
    if len(name) > 200:
        print("Donor name too long (max 200 chars).")
        return

    email_raw = prompt_optional("Email (optional): ")
    if not looks_like_email(email_raw):
        print("Email doesn't look valid. Leave blank if unknown.")
        return
    email = normalize_optional_email(email_raw)

    phone_raw = prompt_optional("Phone (optional): ")
    if not looks_like_phone(phone_raw):
        print("Phone number doesn't look valid. Leave blank if unknown.")
        return
    phone = normalize_optional_phone(phone_raw)

    address = prompt_optional("Address (optional): ")
    if address and len(address) > 300:
        print("Address too long (max 300 chars).")
        return

    def op(_conn: Any, cur: Any) -> int:
        cur.execute(
            "INSERT INTO donors (name, email, phone, address) VALUES (?, ?, ?, ?)",
            (name, email, phone, address),
        )
        return int(cur.lastrowid)

    try:
        donor_id = run_db(op, write=True)
    except sqlite3.IntegrityError as e:
        print(f"Could not add donor (data conflict): {e}")
        return

    print(f"Donor added! (ID: {donor_id})")
    audit("donor.create", entity_type="donor", entity_id=int(donor_id), details={"name": name})


@require_role("admin", "fundraiser", "viewer")
def view_donors() -> None:
    def op(_conn: Any, cur: Any):
        cur.execute("SELECT id, name, email, phone, address FROM donors ORDER BY id DESC")
        return cur.fetchall()

    rows = run_db(op)
    if not rows:
        print("No donors yet.")
        return
    print_table(["ID", "Name", "Email", "Phone", "Address"], rows)


@require_role("admin", "fundraiser")
def tag_donor() -> None:
    donor_id = prompt_int("Donor ID: ")

    def exists_op(_conn: Any, cur: Any) -> bool:
        return _donor_exists(cur, donor_id)

    if not run_db(exists_op):
        print("Donor not found.")
        return

    raw = prompt_non_empty("Tag(s) (comma-separated): ")
    tags_in = [t.strip() for t in raw.split(",")]
    tags: list[str] = []
    for t in tags_in:
        nt = normalize_tag(t)
        if not nt:
            print(f"Invalid tag: '{t}'. Use letters/numbers/spaces/dash/underscore/dot, max 50 chars.")
            return
        tags.append(nt)

    tags = sorted(set(tags))
    if not tags:
        print("No tags provided.")
        return

    def op(conn: Any, cur: Any) -> int:
        added = 0
        for tag in tags:
            cur.execute(
                "INSERT OR IGNORE INTO donor_tags (donor_id, tag, created_at, created_by_user_id) "
                "VALUES (?, ?, ?, ?)",
                (
                    donor_id,
                    tag,
                    utc_now_iso(),
                    int(CURRENT_USER["id"]) if CURRENT_USER and CURRENT_USER.get("id") is not None else None,
                ),
            )
            added += int(cur.rowcount or 0)
        conn.commit()
        return added

    added = run_db(op, write=True)
    print(f"Tags saved. Added {added} new tag(s).")
    audit(
        "donor.tags.add",
        entity_type="donor",
        entity_id=int(donor_id),
        details={"tags": tags, "added": int(added)},
    )


@require_role("admin", "fundraiser", "viewer")
def view_donors_by_tag() -> None:
    raw = prompt_non_empty("Tag: ")
    tag = normalize_tag(raw)
    if not tag:
        print("Invalid tag.")
        return

    def op(_conn: Any, cur: Any):
        cur.execute(
            "SELECT donors.id, donors.name, donors.email, donors.phone "
            "FROM donor_tags JOIN donors ON donors.id = donor_tags.donor_id "
            "WHERE donor_tags.tag = ? "
            "ORDER BY donors.id DESC",
            (tag,),
        )
        return cur.fetchall() or []

    rows = run_db(op)
    if not rows:
        print("No donors found for that tag.")
        return
    print_table(["Donor ID", "Name", "Email", "Phone"], rows)


@require_role("admin", "fundraiser")
def update_donor() -> None:
    donor_id = prompt_int("Donor ID to update: ")

    def fetch_op(_conn: Any, cur: Any):
        cur.execute("SELECT id, name, email, phone, address FROM donors WHERE id = ?", (donor_id,))
        return cur.fetchone()

    donor = run_db(fetch_op)
    if not donor:
        print("Donor not found.")
        return

    _id, cur_name, cur_email, cur_phone, cur_address = donor
    print("\nCurrent donor:")
    print_table(["ID", "Name", "Email", "Phone", "Address"], [donor])

    print("\nEnter new values (blank to keep, '-' to clear optional fields).")

    name_in = prompt_optional("New name (optional): ").strip()
    if name_in:
        if len(name_in) > 200:
            print("Donor name too long (max 200 chars).")
            return
        new_name: str | None = name_in
    else:
        new_name = None

    email_in = prompt_optional("New email (optional): ").strip()
    if email_in == "-":
        new_email = None
    elif email_in:
        if not looks_like_email(email_in):
            print("Email doesn't look valid.")
            return
        new_email = normalize_optional_email(email_in)
    else:
        new_email = "__KEEP__"

    phone_in = prompt_optional("New phone (optional): ").strip()
    if phone_in == "-":
        new_phone = None
    elif phone_in:
        if not looks_like_phone(phone_in):
            print("Phone number doesn't look valid.")
            return
        new_phone = normalize_optional_phone(phone_in)
    else:
        new_phone = "__KEEP__"

    address_in = prompt_optional("New address (optional): ")
    address_in = address_in.strip()
    if address_in == "-":
        new_address = None
    elif address_in:
        if len(address_in) > 300:
            print("Address too long (max 300 chars).")
            return
        new_address = address_in
    else:
        new_address = "__KEEP__"

    def op(conn: Any, cur: Any) -> None:
        cur.execute("SELECT name, email, phone, address FROM donors WHERE id = ?", (donor_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError("Donor not found")
        existing_name, existing_email, existing_phone, existing_address = row

        final_name = new_name if new_name is not None else existing_name
        final_email = existing_email if new_email == "__KEEP__" else new_email
        final_phone = existing_phone if new_phone == "__KEEP__" else new_phone
        final_address = existing_address if new_address == "__KEEP__" else new_address

        cur.execute(
            "UPDATE donors SET name = ?, email = ?, phone = ?, address = ? WHERE id = ?",
            (final_name, final_email, final_phone, final_address, donor_id),
        )
        conn.commit()

    try:
        run_db(op, write=True)
    except sqlite3.IntegrityError as e:
        print(f"Could not update donor (data conflict): {e}")
        return
    except ValueError as e:
        print(e)
        return

    print("Donor updated.")
    audit(
        "donor.update",
        entity_type="donor",
        entity_id=int(donor_id),
        details={
            "updated_by": CURRENT_USER.get("username") if CURRENT_USER else None,
        },
    )


@require_role("admin")
def delete_donor() -> None:
    donor_id = prompt_int("Donor ID to delete: ")

    def check_op(_conn: Any, cur: Any):
        cur.execute("SELECT id, name, email, phone FROM donors WHERE id = ?", (donor_id,))
        donor = cur.fetchone()
        if not donor:
            return None

        cur.execute("SELECT COALESCE(COUNT(*), 0) FROM donations WHERE donor_id = ?", (donor_id,))
        donations_count = int((cur.fetchone() or [0])[0] or 0)

        cur.execute("SELECT COALESCE(COUNT(*), 0) FROM pledges WHERE donor_id = ?", (donor_id,))
        pledges_count = int((cur.fetchone() or [0])[0] or 0)

        cur.execute("SELECT COALESCE(COUNT(*), 0) FROM donor_interactions WHERE donor_id = ?", (donor_id,))
        interactions_count = int((cur.fetchone() or [0])[0] or 0)

        return donor, donations_count, pledges_count, interactions_count

    result = run_db(check_op)
    if not result:
        print("Donor not found.")
        return

    donor, donations_count, pledges_count, interactions_count = result
    print("\nDonor:")
    print_table(["ID", "Name", "Email", "Phone"], [donor])

    if donations_count or pledges_count or interactions_count:
        print("\nCannot delete donor with related records.")
        print(f"Donations: {donations_count}")
        print(f"Pledges: {pledges_count}")
        print(f"Interactions: {interactions_count}")
        print("Delete/migrate related records first.")
        return

    confirm = prompt_optional("Type DELETE to confirm: ").strip()
    if confirm != "DELETE":
        print("Cancelled.")
        return


    def delete_op(conn: Any, cur: Any) -> None:
        # Soft delete: set deleted_at and deleted_by
        cur.execute(
            "UPDATE donors SET deleted_at = ?, deleted_by = ? WHERE id = ?",
            (utc_now_iso(), int(CURRENT_USER["id"]) if CURRENT_USER and CURRENT_USER.get("id") is not None else None, donor_id)
        )
        conn.commit()

    try:
        run_db(delete_op, write=True)
    except sqlite3.Error as e:
        print(f"Could not delete donor: {e}")
        return

    print("Donor marked as deleted (soft delete).")
    audit(
        "donor.soft_delete",
        entity_type="donor",
        entity_id=int(donor_id),
        details={"deleted_by": CURRENT_USER.get("username") if CURRENT_USER else None},
    )
