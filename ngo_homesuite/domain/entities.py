from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class LifecycleState(str, Enum):
    draft = "draft"
    active = "active"
    paused = "paused"
    completed = "completed"
    archived = "archived"


@dataclass(slots=True)
class AuditEntry:
    event: str
    actor: str
    at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class BaseEntity:
    entity_id: str
    name: str
    lifecycle_state: LifecycleState = LifecycleState.draft
    relationships: dict[str, list[str]] = field(default_factory=dict)
    audit_trail: list[AuditEntry] = field(default_factory=list)

    def transition(self, to_state: LifecycleState, actor: str, reason: str = "") -> None:
        old = self.lifecycle_state
        self.lifecycle_state = to_state
        self.audit_trail.append(
            AuditEntry(
                event="state_transition",
                actor=actor,
                details={"from": old.value, "to": to_state.value, "reason": reason},
            )
        )

    def link(self, relation: str, target_id: str, actor: str) -> None:
        bucket = self.relationships.setdefault(relation, [])
        if target_id not in bucket:
            bucket.append(target_id)
            self.audit_trail.append(
                AuditEntry(
                    event="relationship_linked",
                    actor=actor,
                    details={"relation": relation, "target_id": target_id},
                )
            )


@dataclass(slots=True)
class DonorEntity(BaseEntity):
    donor_type: str = "individual"
    email: str | None = None
    phone: str | None = None


@dataclass(slots=True)
class CampaignEntity(BaseEntity):
    fundraising_goal: float = 0.0
    raised_amount: float = 0.0


@dataclass(slots=True)
class GrantEntity(BaseEntity):
    requested_amount: float = 0.0
    approved_amount: float = 0.0
    status_note: str = ""


@dataclass(slots=True)
class BeneficiaryEntity(BaseEntity):
    program_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ProgramEntity(BaseEntity):
    beneficiary_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class OutcomeEntity(BaseEntity):
    metric_name: str = ""
    metric_value: float = 0.0
    program_id: str | None = None
