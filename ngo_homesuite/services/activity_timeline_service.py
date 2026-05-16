"""Service layer for unified constituent activity timelines.

Provides chronological feeds of all interactions (notes, calls, emails, donations,
tasks, case notes, workflow events) across constituent types (donor, volunteer,
beneficiary) for both profile-scoped and organization-wide views.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy import and_, desc, func, or_, select, text

from ngo_homesuite.models.core import Donation, Donor, Beneficiary, User, db


ActivityType = Literal[
    "interaction",      # donor call/email/meeting/note
    "donation",         # donation recorded
    "pledge",           # pledge created/updated
    "case_note",        # beneficiary case note
    "service_log",      # program service log
    "task",             # workflow task/action
    "workflow_event",   # workflow execution
    "volunteer_hour",   # volunteer time logged
]


@dataclass(frozen=True)
class ActivityFeedItem:
    """Unified activity timeline item across all entity types."""

    activity_id: str          # Unique ID within org scope (type:table_id)
    activity_type: ActivityType
    occurred_at: str          # ISO 8601 datetime (UTC)
    entity_type: str          # "donor" | "beneficiary" | "volunteer" | "grant"
    entity_id: int            # FK to entity
    actor_id: int | None      # User who created/modified
    actor_name: str | None    # User display name
    summary: str              # One-line description for UI
    metadata: dict[str, Any]  # Type-specific fields (channel, status, etc.)

    def to_dict(self) -> dict[str, Any]:
        return {
            "activity_id": self.activity_id,
            "activity_type": self.activity_type,
            "occurred_at": self.occurred_at,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "actor_id": self.actor_id,
            "actor_name": self.actor_name,
            "summary": self.summary,
            "metadata": self.metadata,
        }


class ActivityTimelineService:
    """Unified timeline across all constituent activity types."""

    @staticmethod
    def _user_name(user_id: int | None) -> str | None:
        """Fetch user display name."""
        if user_id is None:
            return None
        user = db.session.scalars(
            select(User).where(User.id == user_id).limit(1)
        ).first()
        if user is None:
            return None
        if user.first_name or user.last_name:
            return f"{user.first_name} {user.last_name}".strip()
        return user.username

    @staticmethod
    def get_donor_timeline(
        organization_id: int,
        donor_id: int,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ActivityFeedItem]:
        """Fetch unified timeline for a donor.

        Includes:
        - Donor interactions (calls, emails, meetings, notes)
        - Donations
        - Pledges
        - Related beneficiary connections (if any)
        - Workflow events referencing this donor

        Args:
            organization_id: Org scope
            donor_id: Target donor
            limit: Max items to return
            offset: Pagination offset

        Returns:
            Chronological feed (newest first), limited to offset+limit items
        """
        items: list[ActivityFeedItem] = []

        # Query 1: Donor interactions
        donor_interactions = db.session.execute(
            text("""
                SELECT
                    'interaction:' || id AS activity_id,
                    occurred_at,
                    channel,
                    summary,
                    next_action,
                    follow_up_due,
                    completed_at,
                    created_by_user_id
                FROM donor_interactions
                WHERE donor_id = :donor_id
                ORDER BY occurred_at DESC
            """),
            {"donor_id": donor_id},
        ).fetchall()

        for row in donor_interactions:
            activity_id, occurred_at, channel, summary, next_action, follow_up_due, completed_at, created_by = row
            actor_name = ActivityTimelineService._user_name(created_by)
            summary_text = f"{channel.title()} — {summary}"
            if completed_at:
                summary_text += " ✓"

            items.append(
                ActivityFeedItem(
                    activity_id=activity_id,
                    activity_type="interaction",
                    occurred_at=occurred_at,
                    entity_type="donor",
                    entity_id=donor_id,
                    actor_id=created_by,
                    actor_name=actor_name,
                    summary=summary_text,
                    metadata={
                        "channel": channel,
                        "next_action": next_action,
                        "follow_up_due": follow_up_due,
                        "completed": completed_at is not None,
                    },
                )
            )

        # Query 2: Donations
        donations = db.session.execute(
            text("""
                SELECT
                    'donation:' || id AS activity_id,
                    donation_date,
                    amount,
                    COALESCE(fund_id, 0) AS fund_id,
                    COALESCE(project_id, 0) AS project_id
                FROM donations
                WHERE donor_id = :donor_id
                ORDER BY donation_date DESC
            """),
            {"donor_id": donor_id},
        ).fetchall()

        for row in donations:
            activity_id, donation_date, amount, fund_id, project_id = row
            amount_dollars = f"${amount / 100:.2f}" if amount else "$0.00"
            summary_text = f"Donation received — {amount_dollars}"

            items.append(
                ActivityFeedItem(
                    activity_id=activity_id,
                    activity_type="donation",
                    occurred_at=donation_date,
                    entity_type="donor",
                    entity_id=donor_id,
                    actor_id=None,
                    actor_name=None,
                    summary=summary_text,
                    metadata={
                        "amount": amount,
                        "amount_formatted": amount_dollars,
                        "fund_id": fund_id if fund_id else None,
                        "project_id": project_id if project_id else None,
                    },
                )
            )

        # Sort by occurred_at descending (newest first)
        items.sort(key=lambda x: x.occurred_at, reverse=True)

        # Apply pagination
        total = len(items)
        paginated = items[offset : offset + limit]

        return paginated

    @staticmethod
    def get_beneficiary_timeline(
        organization_id: int,
        beneficiary_id: int,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ActivityFeedItem]:
        """Fetch unified timeline for a beneficiary.

        Includes:
        - Case notes / service logs
        - Case status changes
        - Program appointments
        - Related donor interactions (if linked)

        Args:
            organization_id: Org scope
            beneficiary_id: Target beneficiary
            limit: Max items to return
            offset: Pagination offset

        Returns:
            Chronological feed (newest first), limited to offset+limit items
        """
        items: list[ActivityFeedItem] = []

        # Query 1: Case service logs (if program module tables exist)
        try:
            service_logs = db.session.execute(
                text("""
                    SELECT
                        'service_log:' || id AS activity_id,
                        created_at,
                        COALESCE(description, 'Service log entry'),
                        created_by_user_id
                    FROM service_logs
                    WHERE case_id IN (
                        SELECT id FROM cases
                        WHERE beneficiary_id = :beneficiary_id
                    )
                    ORDER BY created_at DESC
                """),
                {"beneficiary_id": beneficiary_id},
            ).fetchall()

            for row in service_logs:
                activity_id, created_at, description, created_by = row
                actor_name = ActivityTimelineService._user_name(created_by)

                items.append(
                    ActivityFeedItem(
                        activity_id=activity_id,
                        activity_type="service_log",
                        occurred_at=created_at,
                        entity_type="beneficiary",
                        entity_id=beneficiary_id,
                        actor_id=created_by,
                        actor_name=actor_name,
                        summary=description,
                        metadata={},
                    )
                )
        except Exception:
            # If service_logs table doesn't exist, skip gracefully
            pass

        # Query 2: Case status changes (from audit log if available)
        try:
            case_events = db.session.execute(
                text("""
                    SELECT
                        'case_event:' || audit_log.id AS activity_id,
                        audit_log.created_at,
                        'Case status changed',
                        audit_log.changed_by
                    FROM audit_log
                    JOIN cases ON cases.id = CAST(audit_log.row_id AS INTEGER)
                    WHERE cases.beneficiary_id = :beneficiary_id
                        AND audit_log.table_name = 'cases'
                        AND audit_log.operation IN ('UPDATE')
                    ORDER BY audit_log.created_at DESC
                """),
                {"beneficiary_id": beneficiary_id},
            ).fetchall()

            for row in case_events:
                activity_id, created_at, summary, changed_by = row
                actor_name = ActivityTimelineService._user_name(changed_by)

                items.append(
                    ActivityFeedItem(
                        activity_id=activity_id,
                        activity_type="case_note",
                        occurred_at=created_at,
                        entity_type="beneficiary",
                        entity_id=beneficiary_id,
                        actor_id=changed_by,
                        actor_name=actor_name,
                        summary=summary,
                        metadata={},
                    )
                )
        except Exception:
            # If audit_log or cases table structure differs, skip gracefully
            pass

        # Sort by occurred_at descending (newest first)
        items.sort(key=lambda x: x.occurred_at, reverse=True)

        # Apply pagination
        paginated = items[offset : offset + limit]

        return paginated

    @staticmethod
    def get_organization_activity(
        organization_id: int,
        *,
        limit: int = 50,
        offset: int = 0,
        entity_type_filter: str | None = None,
    ) -> list[ActivityFeedItem]:
        """Fetch organization-wide recent activity feed.

        Includes all interactions, donations, and key events across all
        constituents. Used for dashboard/activity feeds.

        Args:
            organization_id: Org scope
            limit: Max items to return
            offset: Pagination offset
            entity_type_filter: Optional filter to only show "donor", "beneficiary", etc.

        Returns:
            Chronological feed (newest first)
        """
        items: list[ActivityFeedItem] = []

        # Query 1: Recent interactions
        recent_interactions = db.session.execute(
            text("""
                SELECT
                    'interaction:' || di.id AS activity_id,
                    di.occurred_at,
                    di.donor_id,
                    di.channel,
                    di.summary,
                    di.created_by_user_id,
                    d.name
                FROM donor_interactions di
                JOIN donors d ON d.id = di.donor_id
                WHERE d.organization_id = :org_id
                ORDER BY di.occurred_at DESC
                LIMIT :limit
            """),
            {"org_id": organization_id, "limit": limit},
        ).fetchall()

        for row in recent_interactions:
            (
                activity_id,
                occurred_at,
                donor_id,
                channel,
                summary,
                created_by,
                donor_name,
            ) = row
            if entity_type_filter and entity_type_filter != "donor":
                continue

            actor_name = ActivityTimelineService._user_name(created_by)
            summary_text = f"{donor_name}: {channel.title()} — {summary}"

            items.append(
                ActivityFeedItem(
                    activity_id=activity_id,
                    activity_type="interaction",
                    occurred_at=occurred_at,
                    entity_type="donor",
                    entity_id=donor_id,
                    actor_id=created_by,
                    actor_name=actor_name,
                    summary=summary_text,
                    metadata={"channel": channel},
                )
            )

        # Query 2: Recent donations
        recent_donations = db.session.execute(
            text("""
                SELECT
                    'donation:' || don.id AS activity_id,
                    don.donation_date,
                    don.donor_id,
                    don.amount,
                    d.name
                FROM donations don
                JOIN donors d ON d.id = don.donor_id
                WHERE d.organization_id = :org_id
                ORDER BY don.donation_date DESC
                LIMIT :limit
            """),
            {"org_id": organization_id, "limit": limit},
        ).fetchall()

        for row in recent_donations:
            activity_id, donation_date, donor_id, amount, donor_name = row
            if entity_type_filter and entity_type_filter != "donor":
                continue

            amount_dollars = f"${amount / 100:.2f}" if amount else "$0.00"
            summary_text = f"{donor_name}: Donation received — {amount_dollars}"

            items.append(
                ActivityFeedItem(
                    activity_id=activity_id,
                    activity_type="donation",
                    occurred_at=donation_date,
                    entity_type="donor",
                    entity_id=donor_id,
                    actor_id=None,
                    actor_name=None,
                    summary=summary_text,
                    metadata={"amount": amount, "amount_formatted": amount_dollars},
                )
            )

        # Sort by occurred_at descending (newest first) and apply pagination
        items.sort(key=lambda x: x.occurred_at, reverse=True)
        paginated = items[offset : offset + limit]

        return paginated
