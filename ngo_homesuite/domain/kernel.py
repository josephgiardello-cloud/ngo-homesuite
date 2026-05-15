from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum


class EntityLifecycle(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    CLOSED = "closed"


@dataclass
class OrganizationRoot:
    org_id: str
    name: str


@dataclass
class Beneficiary:
    beneficiary_id: str
    org_id: str
    display_name: str
    household_id: str | None = None
    lifecycle: EntityLifecycle = EntityLifecycle.ACTIVE


@dataclass
class Household:
    household_id: str
    org_id: str
    primary_contact_name: str
    member_count: int = 1


@dataclass
class CaseIntake:
    case_id: str
    org_id: str
    beneficiary_id: str
    status: str = "intake"


@dataclass
class Program:
    program_id: str
    org_id: str
    name: str
    lifecycle: EntityLifecycle = EntityLifecycle.ACTIVE


@dataclass
class ServiceDeliveryEvent:
    service_event_id: str
    org_id: str
    case_id: str
    program_id: str
    notes: str = ""
    delivered_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class Donation:
    donation_id: str
    org_id: str
    donor_name: str
    amount: float
    currency: str = "USD"


@dataclass
class Volunteer:
    volunteer_id: str
    org_id: str
    display_name: str


@dataclass
class StaffUser:
    staff_user_id: str
    org_id: str
    display_name: str
    role: str
