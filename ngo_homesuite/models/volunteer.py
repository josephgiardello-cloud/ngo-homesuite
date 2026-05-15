from __future__ import annotations

from typing import Any

from ..auth.session import require_role
from ..db.connection import run_db
from ..db.utils import print_table
from ..prompts import (
    looks_like_email,
    looks_like_phone,
    normalize_optional_email,
    normalize_optional_phone,
    prompt_non_empty,
    prompt_optional,
)


@require_role("admin")
def add_volunteer() -> None:
    name = prompt_non_empty("Volunteer name: ")

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

    skills = prompt_optional("Skills (optional): ")
    availability = prompt_optional("Availability (optional): ")

    def op(_conn: Any, cur: Any) -> None:
        cur.execute(
            "INSERT INTO volunteers (name, email, phone, skills, availability) VALUES (?, ?, ?, ?, ?)",
            (name, email, phone, skills, availability),
        )

    run_db(op, write=True)
    print("Volunteer added!")


@require_role("admin")
def view_volunteers() -> None:
    def op(_conn: Any, cur: Any):
        cur.execute(
            "SELECT id, name, email, phone, skills, availability FROM volunteers ORDER BY id DESC"
        )
        return cur.fetchall()

    rows = run_db(op)
    if not rows:
        print("No volunteers yet.")
        return
    print_table(["ID", "Name", "Email", "Phone", "Skills", "Availability"], rows)
