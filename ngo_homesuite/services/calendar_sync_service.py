from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import re
from typing import Any, Protocol

from sqlalchemy import select

from ngo_homesuite.models.core import Donor, Task, db


class CalendarProvider(Protocol):
    def upsert_event(self, *, event_key: str, title: str, due_date: datetime, metadata: dict[str, Any]) -> None:
        ...


@dataclass
class InMemoryCalendarProvider:
    events: dict[str, dict[str, Any]] = field(default_factory=dict)

    def upsert_event(self, *, event_key: str, title: str, due_date: datetime, metadata: dict[str, Any]) -> None:
        self.events[event_key] = {
            "title": title,
            "due_date": due_date,
            "metadata": dict(metadata),
        }


class DavSyncProvider(Protocol):
    def upsert_caldav_event(self, *, event_key: str, ical_payload: str, metadata: dict[str, Any]) -> None:
        ...

    def upsert_carddav_contact(self, *, contact_key: str, vcard_payload: str, metadata: dict[str, Any]) -> None:
        ...


@dataclass
class InMemoryDavSyncProvider:
    caldav_events: dict[str, dict[str, Any]] = field(default_factory=dict)
    carddav_contacts: dict[str, dict[str, Any]] = field(default_factory=dict)

    def upsert_caldav_event(self, *, event_key: str, ical_payload: str, metadata: dict[str, Any]) -> None:
        self.caldav_events[event_key] = {
            "ical": str(ical_payload),
            "metadata": dict(metadata),
        }

    def upsert_carddav_contact(self, *, contact_key: str, vcard_payload: str, metadata: dict[str, Any]) -> None:
        self.carddav_contacts[contact_key] = {
            "vcard": str(vcard_payload),
            "metadata": dict(metadata),
        }


def _ical_escape(value: str) -> str:
    text = str(value or "")
    text = text.replace("\\", "\\\\")
    text = text.replace(";", "\\;")
    text = text.replace(",", "\\,")
    text = text.replace("\n", "\\n")
    return text


def _vcard_escape(value: str) -> str:
    text = str(value or "")
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _slug_for_uid(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or "").strip().lower()).strip("-")
    return slug or "item"


def _task_to_ical(task: Task) -> str:
    due_raw = task.due_date
    if isinstance(due_raw, datetime):
        due = due_raw
    elif isinstance(due_raw, str):
        try:
            due = datetime.fromisoformat(due_raw.replace("Z", "+00:00"))
        except ValueError:
            due = datetime.utcnow()
    else:
        due = datetime.utcnow()
    due_utc = due.strftime("%Y%m%dT%H%M%SZ")
    now_utc = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    uid = f"task-{int(task.id)}@ngo-homesuite"
    summary = _ical_escape(str(task.title or "Task"))
    desc = _ical_escape(str(task.description or ""))

    return (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//NGO HomeSuite//Task Sync//EN\r\n"
        "BEGIN:VTODO\r\n"
        f"UID:{uid}\r\n"
        f"DTSTAMP:{now_utc}\r\n"
        f"DUE:{due_utc}\r\n"
        f"SUMMARY:{summary}\r\n"
        f"DESCRIPTION:{desc}\r\n"
        f"STATUS:{'COMPLETED' if str(task.status or '').lower() == 'done' else 'NEEDS-ACTION'}\r\n"
        "END:VTODO\r\n"
        "END:VCALENDAR\r\n"
    )


def _donor_to_vcard(donor: Donor) -> str:
    uid = f"donor-{int(donor.id)}@ngo-homesuite"
    name = _vcard_escape(str(donor.name or "Donor"))
    email = _vcard_escape(str(donor.email or ""))
    phone = _vcard_escape(str(donor.phone or ""))
    org = _vcard_escape(str(getattr(donor.organization, "name", "") or ""))

    lines = [
        "BEGIN:VCARD",
        "VERSION:3.0",
        f"UID:{uid}",
        f"FN:{name}",
        f"N:{name};;;;",
    ]
    if org:
        lines.append(f"ORG:{org}")
    if email:
        lines.append(f"EMAIL;TYPE=INTERNET:{email}")
    if phone:
        lines.append(f"TEL;TYPE=CELL:{phone}")
    lines.append("END:VCARD")
    return "\r\n".join(lines) + "\r\n"



def sync_task_deadlines(
    organization_id: int,
    provider: CalendarProvider,
    *,
    statuses: tuple[str, ...] = ("open", "in_progress"),
) -> dict[str, int]:
    tasks = list(
        db.session.scalars(
            select(Task)
            .where(
                Task.organization_id == organization_id,
                Task.status.in_(list(statuses)),
                Task.due_date.is_not(None),
            )
            .order_by(Task.due_date.asc())
        )
    )

    synced = 0
    skipped = 0
    for task in tasks:
        if task.due_date is None:
            skipped += 1
            continue
        provider.upsert_event(
            event_key=f"task:{task.id}",
            title=task.title,
            due_date=task.due_date,
            metadata={
                "organization_id": task.organization_id,
                "task_id": task.id,
                "status": task.status,
                "priority": task.priority,
            },
        )
        synced += 1

    return {"synced": synced, "skipped": skipped}


def sync_task_deadlines_to_caldav(
    organization_id: int,
    provider: DavSyncProvider,
    *,
    statuses: tuple[str, ...] = ("open", "in_progress"),
) -> dict[str, int]:
    tasks = list(
        db.session.scalars(
            select(Task)
            .where(
                Task.organization_id == organization_id,
                Task.status.in_(list(statuses)),
                Task.due_date.is_not(None),
            )
            .order_by(Task.due_date.asc())
        )
    )

    synced = 0
    skipped = 0
    for task in tasks:
        if task.due_date is None:
            skipped += 1
            continue
        try:
            try:
                ical_payload = _task_to_ical(task)
            except Exception:
                now_utc = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
                summary = _ical_escape(str(getattr(task, "title", "Task") or "Task"))
                ical_payload = (
                    "BEGIN:VCALENDAR\r\n"
                    "VERSION:2.0\r\n"
                    "PRODID:-//NGO HomeSuite//Task Sync//EN\r\n"
                    "BEGIN:VTODO\r\n"
                    f"UID:task-{int(getattr(task, 'id', 0) or 0)}@ngo-homesuite\r\n"
                    f"DTSTAMP:{now_utc}\r\n"
                    f"SUMMARY:{summary}\r\n"
                    "STATUS:NEEDS-ACTION\r\n"
                    "END:VTODO\r\n"
                    "END:VCALENDAR\r\n"
                )

            provider.upsert_caldav_event(
                event_key=f"task-{_slug_for_uid(str(task.id))}.ics",
                ical_payload=ical_payload,
                metadata={
                    "organization_id": int(task.organization_id),
                    "task_id": int(task.id),
                    "status": str(task.status or "open"),
                },
            )
            synced += 1
        except Exception:
            skipped += 1

    return {"synced": synced, "skipped": skipped}


def sync_donor_contacts_to_carddav(
    organization_id: int,
    provider: DavSyncProvider,
) -> dict[str, int]:
    donors = list(
        db.session.scalars(
            select(Donor)
            .where(
                Donor.organization_id == organization_id,
                Donor.email.is_not(None),
            )
            .order_by(Donor.created_at.asc())
        )
    )

    synced = 0
    skipped = 0
    for donor in donors:
        email = str(donor.email or "").strip()
        if not email:
            skipped += 1
            continue
        try:
            try:
                vcard_payload = _donor_to_vcard(donor)
            except Exception:
                name = _vcard_escape(str(getattr(donor, "name", "Donor") or "Donor"))
                vcard_payload = "\r\n".join(
                    [
                        "BEGIN:VCARD",
                        "VERSION:3.0",
                        f"UID:donor-{int(getattr(donor, 'id', 0) or 0)}@ngo-homesuite",
                        f"FN:{name}",
                        f"N:{name};;;;",
                        f"EMAIL;TYPE=INTERNET:{_vcard_escape(email)}",
                        "END:VCARD",
                        "",
                    ]
                )

            provider.upsert_carddav_contact(
                contact_key=f"donor-{_slug_for_uid(str(donor.id))}.vcf",
                vcard_payload=vcard_payload,
                metadata={
                    "organization_id": int(donor.organization_id),
                    "donor_id": int(donor.id),
                    "email": email.lower(),
                },
            )
            synced += 1
        except Exception:
            skipped += 1

    return {"synced": synced, "skipped": skipped}
