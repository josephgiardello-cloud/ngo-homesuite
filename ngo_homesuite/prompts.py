from __future__ import annotations

import re
from datetime import datetime, timezone, date

from typing import Any

from .config import MAX_EMAIL_LENGTH, PHONE_MAX_DIGITS, PHONE_MIN_DIGITS


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def utc_today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def utc_now_compact() -> str:
    # For filenames: 20251228_235959
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def prompt_non_empty(label: str) -> str:
    while True:
        value = input(label).strip()
        if value:
            return value
        print("Value is required.")


def prompt_optional(label: str) -> str:
    return input(label).strip()


def _prompt_non_empty(label: str) -> str:
    return prompt_non_empty(label)


def _prompt_optional(label: str) -> str:
    return prompt_optional(label)


def prompt_int(label: str) -> int:
    while True:
        raw = input(label).strip()
        try:
            return int(raw)
        except ValueError:
            print("Please enter a valid integer.")


def _prompt_int(label: str) -> int:
    return prompt_int(label)


def prompt_positive_amount(label: str) -> float:
    while True:
        raw = input(label).strip()
        try:
            value = float(raw)
        except ValueError:
            print("Please enter a valid number.")
            continue
        if value <= 0:
            print("Amount must be greater than 0.")
            continue
        return value


def _prompt_positive_amount(label: str) -> float:
    return prompt_positive_amount(label)


def prompt_non_negative_amount(label: str) -> float:
    while True:
        raw = input(label).strip()
        try:
            value = float(raw)
        except ValueError:
            print("Please enter a valid number.")
            continue
        if value < 0:
            print("Amount must be 0 or greater.")
            continue
        return value


def _prompt_non_negative_amount(label: str) -> float:
    return prompt_non_negative_amount(label)


def prompt_positive_amount_allowing_zero(label: str) -> float:
    """Prompts for a number >= 0, but nudges users away from zero allocations."""

    value = prompt_non_negative_amount(label)
    if value == 0:
        print("Note: 0 has no effect.")
    return value


def _prompt_positive_amount_allowing_zero(label: str) -> float:
    return prompt_positive_amount_allowing_zero(label)


def prompt_date_iso(label: str) -> str:
    """Prompt for a date in YYYY-MM-DD format."""

    while True:
        raw = prompt_non_empty(label).strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw) is None:
            print("Please use YYYY-MM-DD.")
            continue
        try:
            datetime.fromisoformat(raw)
        except ValueError:
            print("Invalid date.")
            continue
        return raw


def _prompt_date_iso(label: str) -> str:
    return prompt_date_iso(label)


def looks_like_email(value: str) -> bool:
    value = value.strip()
    if not value:
        return True
    if len(value) > MAX_EMAIL_LENGTH:
        return False
    return re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value) is not None


def _looks_like_email(value: str) -> bool:
    return looks_like_email(value)


def normalize_optional_email(value: str) -> str | None:
    value = value.strip()
    return value or None


def _normalize_optional_email(value: str) -> str | None:
    return normalize_optional_email(value)


def looks_like_phone(value: str) -> bool:
    value = value.strip()
    if not value:
        return True
    if re.fullmatch(r"[0-9+()\-\.\s]+", value) is None:
        return False
    digits = re.sub(r"\D", "", value)
    return PHONE_MIN_DIGITS <= len(digits) <= PHONE_MAX_DIGITS


def _looks_like_phone(value: str) -> bool:
    return looks_like_phone(value)


def normalize_optional_phone(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None

    plus = value.lstrip().startswith("+")
    digits = re.sub(r"\D", "", value)
    if not (PHONE_MIN_DIGITS <= len(digits) <= PHONE_MAX_DIGITS):
        return None
    return f"+{digits}" if plus else digits


def _normalize_optional_phone(value: str) -> str | None:
    return normalize_optional_phone(value)


def normalize_tag(value: str) -> str | None:
    value = re.sub(r"\s+", " ", value.strip().lower())
    if not value:
        return None
    if len(value) > 50:
        return None
    if re.fullmatch(r"[a-z0-9 _\-\.]+", value) is None:
        return None
    return value


def _normalize_tag(value: str) -> str | None:
    return normalize_tag(value)


def print_table(headers: list[str], rows: list[tuple[Any, ...]]) -> None:
    if not rows:
        print("(none)")
        return

    col_count = len(headers)
    normalized_rows: list[list[str]] = []
    for r in rows:
        cells = list(r[:col_count]) + [""] * max(0, col_count - len(r))
        normalized_rows.append(["" if c is None else str(c) for c in cells])

    widths = [len(h) for h in headers]
    for r in normalized_rows:
        for i, cell in enumerate(r):
            widths[i] = max(widths[i], len(cell))

    header_line = " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    sep_line = "-+-".join("-" * widths[i] for i in range(col_count))
    print(header_line)
    print(sep_line)
    for r in normalized_rows:
        print(" | ".join(r[i].ljust(widths[i]) for i in range(col_count)))


def _print_table(headers: list[str], rows: list[tuple[Any, ...]]) -> None:
    print_table(headers, rows)


def parse_utc_iso(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_date_iso_to_date(value: str) -> date | None:
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()) is None:
            return None
        return date.fromisoformat(value.strip())
    except (TypeError, ValueError):
        return None
