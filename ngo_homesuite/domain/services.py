from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from ..prompts import looks_like_email, looks_like_phone, utc_now_iso, utc_today_iso
from .store import DomainError, TenantStore

Money = str | int | float | Decimal
Entity = dict[str, object]
GrantAction = Literal["approve", "pay", "cancel"]


def inserted_id(cursor: sqlite3.Cursor) -> int:
    value = cursor.lastrowid
    if value is None:
        raise RuntimeError("Database did not return an inserted entity ID")
    return value


def minor_units(value: Money, *, zero: bool = False) -> int:
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise DomainError("Amount must be a finite monetary value") from exc
    if not amount.is_finite() or amount < 0 or (amount == 0 and not zero):
        raise DomainError("Amount must be positive" if not zero else "Amount must not be negative")
    if amount > Decimal("999999999999.99") or amount * 100 != (amount * 100).to_integral_value():
        raise DomainError("Amount must have at most two decimals and be within the supported limit")
    return int(amount * 100)


def text(value: str, label: str, limit: int, *, optional: bool = False) -> str:
    value = value.strip()
    if (not value and not optional) or len(value) > limit:
        raise DomainError(f"{label} is required and must be at most {limit} characters")
    return value


@dataclass(frozen=True)
class Actor:
    tenant: str
    user_id: int


@dataclass(frozen=True)
class Allocation:
    category: str
    project_id: int | None
    amount: Money


class Services:
    def __init__(self, store: TenantStore) -> None:
        self.store = store

    @staticmethod
    def authorize(conn: sqlite3.Connection, actor: Actor, *roles: str) -> sqlite3.Row:
        user = conn.execute("SELECT id, username, role FROM users WHERE id = ?", (actor.user_id,)).fetchone()
        if user is None or user["role"] not in roles:
            raise DomainError("Access denied", 403)
        return user

    @staticmethod
    def event(
        conn: sqlite3.Connection, actor: Actor, action: str, entity: str, entity_id: int,
        details: Entity,
    ) -> None:
        user = conn.execute("SELECT username, role FROM users WHERE id = ?", (actor.user_id,)).fetchone()
        payload = json.dumps(
            {"tenant": actor.tenant, "action": action, "entity_type": entity,
             "entity_id": entity_id, "details": details},
            sort_keys=True, allow_nan=False,
        )
        cursor = conn.execute(
            """INSERT INTO audit_log(at_utc, user_id, username, role, action, entity_type,
               entity_id, details_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (utc_now_iso(), actor.user_id, user["username"], user["role"], action, entity, entity_id, payload),
        )
        conn.execute("INSERT INTO outbox(audit_id, payload) VALUES (?, ?)", (cursor.lastrowid, payload))

    @staticmethod
    def get(conn: sqlite3.Connection, table: str, entity_id: int) -> sqlite3.Row:
        # Only fixed internal table names are accepted by service callers.
        if table not in {"donors", "donations", "projects", "pledges", "campaigns", "grants"}:
            raise ValueError("Unsupported entity")
        row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (entity_id,)).fetchone()
        if row is None:
            raise DomainError(f"{table.rstrip('s').title()} not found", 404)
        return row

    def list_entities(self, actor: Actor, entity: str) -> list[Entity]:
        if entity not in {"donors", "donations", "projects", "grants", "audit_log"}:
            raise DomainError("Unknown resource", 404)
        with self.store.transaction(actor.tenant) as conn:
            self.authorize(conn, actor, *(("admin",) if entity == "audit_log" else ("admin", "fundraiser", "viewer")))
            return [dict(row) for row in conn.execute(f"SELECT * FROM {entity} ORDER BY id DESC LIMIT 500")]

    def create_donor(
        self, actor: Actor, name: str, email: str = "", phone: str = "", address: str = "",
    ) -> int:
        name = text(name, "Name", 200)
        address = text(address, "Address", 300, optional=True)
        email, phone = email.strip(), phone.strip()
        if not looks_like_email(email) or not looks_like_phone(phone):
            raise DomainError("Invalid email or phone")
        with self.store.transaction(actor.tenant, write=True) as conn:
            self.authorize(conn, actor, "admin", "fundraiser")
            try:
                cursor = conn.execute(
                    "INSERT INTO donors(name, email, phone, address) VALUES (?, ?, ?, ?)",
                    (name, email or None, phone or None, address),
                )
            except sqlite3.IntegrityError as exc:
                raise DomainError("Donor email already exists", 409) from exc
            entity_id = inserted_id(cursor)
            self.event(conn, actor, "donor.create", "donor", entity_id, {"name": name})
            return entity_id

    def record_donation(
        self, actor: Actor, donor_id: int, amount: Money, idempotency_key: str,
        *, purpose: str = "", campaign_id: int | None = None, pledge_id: int | None = None,
        donated_on: str | None = None,
    ) -> int:
        cents = minor_units(amount)
        purpose = text(purpose, "Purpose", 250, optional=True)
        key = text(idempotency_key, "Idempotency key", 128)
        if donated_on is not None:
            try:
                donated_on = date.fromisoformat(donated_on).isoformat()
            except ValueError as exc:
                raise DomainError("Date must use YYYY-MM-DD") from exc
        # Omitted date stays omitted in the fingerprint so a retry across midnight is identical.
        fingerprint = hashlib.sha256(json.dumps(
            [donor_id, cents, purpose, campaign_id, pledge_id, donated_on],
            separators=(",", ":"),
        ).encode()).hexdigest()
        with self.store.transaction(actor.tenant, write=True) as conn:
            self.authorize(conn, actor, "admin", "fundraiser")
            existing = conn.execute(
                "SELECT id, request_hash FROM donations WHERE idempotency_key = ?", (key,),
            ).fetchone()
            if existing:
                if existing["request_hash"] != fingerprint:
                    raise DomainError("Idempotency key was used for another request", 409)
                return int(existing["id"])
            self.get(conn, "donors", donor_id)
            if pledge_id is not None:
                pledge = self.get(conn, "pledges", pledge_id)
                if pledge["donor_id"] != donor_id:
                    raise DomainError("Pledge belongs to another donor")
                if campaign_id is None:
                    campaign_id = pledge["campaign_id"]
                elif pledge["campaign_id"] is not None and campaign_id != pledge["campaign_id"]:
                    raise DomainError("Campaign does not match pledge")
            if campaign_id is not None:
                self.get(conn, "campaigns", campaign_id)
            cursor = conn.execute(
                """INSERT INTO donations(donor_id, campaign_id, pledge_id, amount, date, purpose,
                   idempotency_key, request_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (donor_id, campaign_id, pledge_id, cents / 100,
                 donated_on or utc_today_iso(), purpose, key, fingerprint),
            )
            entity_id = inserted_id(cursor)
            self.event(conn, actor, "donation.create", "donation", entity_id,
                       {"donor_id": donor_id, "amount_minor": cents})
            return entity_id

    def allocate_donation(self, actor: Actor, donation_id: int, allocations: list[Allocation]) -> None:
        values = [(a.category, a.project_id, minor_units(a.amount)) for a in allocations]
        with self.store.transaction(actor.tenant, write=True) as conn:
            self.authorize(conn, actor, "admin", "fundraiser")
            donation = self.get(conn, "donations", donation_id)
            if sum(value[2] for value in values) > minor_units(donation["amount"]):
                raise DomainError("Allocations exceed donation amount")
            for category, project_id, _ in values:
                if category not in {"project", "payroll", "general"}:
                    raise DomainError("Unknown allocation category")
                if category == "project":
                    if project_id is None:
                        raise DomainError("Project allocation requires a project")
                    self.get(conn, "projects", project_id)
                elif project_id is not None:
                    raise DomainError("Only project allocations may specify a project")
            conn.execute("DELETE FROM donation_allocations WHERE donation_id = ?", (donation_id,))
            conn.executemany(
                "INSERT INTO donation_allocations(donation_id, category, project_id, amount) VALUES (?, ?, ?, ?)",
                [(donation_id, category, project_id, cents / 100) for category, project_id, cents in values],
            )
            self.event(conn, actor, "donation.allocate.multi", "donation", donation_id,
                       {"amount_minor": sum(v[2] for v in values), "count": len(values)})

    def create_project(self, actor: Actor, name: str, budget: Money, description: str = "") -> int:
        name = text(name, "Name", 200)
        description = text(description, "Description", 2000, optional=True)
        cents = minor_units(budget)
        with self.store.transaction(actor.tenant, write=True) as conn:
            self.authorize(conn, actor, "admin")
            cursor = conn.execute(
                "INSERT INTO projects(name, budget, spent, description, start_date) VALUES (?, ?, 0, ?, ?)",
                (name, cents / 100, description, utc_today_iso()),
            )
            entity_id = inserted_id(cursor)
            self.event(conn, actor, "project.create", "project", entity_id, {"budget_minor": cents})
            return entity_id

    @staticmethod
    def reserved(conn: sqlite3.Connection, project_id: int) -> int:
        return int(conn.execute(
            "SELECT COALESCE(SUM(amount_minor), 0) FROM grants WHERE project_id = ? AND status = 'approved'",
            (project_id,),
        ).fetchone()[0])

    def set_project_spent(self, actor: Actor, project_id: int, amount: Money) -> None:
        cents = minor_units(amount, zero=True)
        with self.store.transaction(actor.tenant, write=True) as conn:
            self.authorize(conn, actor, "admin")
            project = self.get(conn, "projects", project_id)
            paid = int(conn.execute(
                "SELECT COALESCE(SUM(amount_minor), 0) FROM grants WHERE project_id = ? AND status = 'paid'",
                (project_id,),
            ).fetchone()[0])
            if cents < paid or cents + self.reserved(conn, project_id) > minor_units(project["budget"]):
                raise DomainError("Spent must cover paid grants and leave room for approved grants within budget")
            conn.execute("UPDATE projects SET spent = ? WHERE id = ?", (cents / 100, project_id))
            self.event(conn, actor, "project.spent", "project", project_id, {"amount_minor": cents})

    def create_grant(self, actor: Actor, project_id: int, name: str, amount: Money) -> int:
        name = text(name, "Name", 200)
        cents = minor_units(amount)
        with self.store.transaction(actor.tenant, write=True) as conn:
            self.authorize(conn, actor, "admin", "fundraiser")
            self.get(conn, "projects", project_id)
            cursor = conn.execute(
                "INSERT INTO grants(project_id, name, amount_minor, status, created_by) VALUES (?, ?, ?, 'draft', ?)",
                (project_id, name, cents, actor.user_id),
            )
            entity_id = inserted_id(cursor)
            self.event(conn, actor, "grant.create", "grant", entity_id, {"amount_minor": cents})
            return entity_id

    def transition_grant(self, actor: Actor, grant_id: int, action: GrantAction) -> None:
        with self.store.transaction(actor.tenant, write=True) as conn:
            self.authorize(conn, actor, "admin")
            grant = self.get(conn, "grants", grant_id)
            status = grant["status"]
            project = self.get(conn, "projects", grant["project_id"])
            spent = minor_units(project["spent"] or 0, zero=True)
            budget = minor_units(project["budget"])
            if action == "approve" and status == "draft":
                if spent + self.reserved(conn, project["id"]) + grant["amount_minor"] > budget:
                    raise DomainError("Grant exceeds available project budget", 409)
                conn.execute("UPDATE grants SET status = 'approved', approved_by = ? WHERE id = ?",
                             (actor.user_id, grant_id))
            elif action == "pay" and status == "approved":
                if grant["approved_by"] == actor.user_id:
                    raise DomainError("Payment requires an admin other than the approving admin", 403)
                if spent + self.reserved(conn, project["id"]) > budget:
                    raise DomainError("Project budget no longer covers approved grants", 409)
                conn.execute("UPDATE projects SET spent = ? WHERE id = ?",
                             ((spent + grant["amount_minor"]) / 100, project["id"]))
                conn.execute("UPDATE grants SET status = 'paid', paid_by = ? WHERE id = ?",
                             (actor.user_id, grant_id))
            elif action == "cancel" and status in {"draft", "approved"}:
                conn.execute("UPDATE grants SET status = 'cancelled' WHERE id = ?", (grant_id,))
            else:
                raise DomainError(f"Cannot {action} a {status} grant", 409)
            self.event(conn, actor, f"grant.{action}", "grant", grant_id, {"previous_status": status})
